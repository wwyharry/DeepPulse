"""在线取数唯一入口 — 股票数据可选化的兜底层

本地不预下载行情也能用：查询层缺数时调本模块在线拉取（默认不写库、每次
重新拉取）；`data.cache_to_db` 开启时才写回本地库缓存。全部函数不抛裸异常，
失败统一抛 OnlineFetchError（message 含可操作提示）。

会话内存缓存：同参数当天内不重复拉取，跨自然日自动作废（不落盘，兼容
「默认不入库」决策）。本模块不 import 采集链路的写库函数（除 maybe_cache_*），
采集器（collector/database）也不得反过来依赖本模块的取数函数。
"""

from __future__ import annotations

import threading
import time
from datetime import date

import pandas as pd

from deeppulse import config

# ── 会话缓存（key → (payload, 自然日)） ──────────────────────────────────────

_cache: dict[tuple, tuple] = {}
_cache_lock = threading.Lock()


def _cache_get(key: tuple):
    with _cache_lock:
        hit = _cache.get(key)
    if hit is None:
        return None
    payload, day = hit
    if day != date.today():
        with _cache_lock:
            _cache.pop(key, None)
        return None
    return payload


def _cache_put(key: tuple, payload) -> None:
    with _cache_lock:
        _cache[key] = (payload, date.today())


def clear_session_cache() -> None:
    """清空会话缓存（测试用）"""
    with _cache_lock:
        _cache.clear()


class OnlineFetchError(RuntimeError):
    """在线取数彻底失败（所有源超时/异常）。message 需含可操作提示。"""


_HINT = "本地无数据，且在线获取失败（网络不可用或数据源限频）。请检查网络后重试，或运行 /fetch update 采集到本地。"


def _source_names() -> list[str]:
    """在线取数的数据源降级链：default_source 优先，其余按 DATA_SOURCES 顺序殿后。

    始终返回完整链条（而非只回首选）——首选源失败/无数据时自动切换下一源。
    """
    names = list(config.DATA_SOURCES)
    if config.DATA_DEFAULT_SOURCE and config.DATA_DEFAULT_SOURCE in names:
        names.remove(config.DATA_DEFAULT_SOURCE)
        names.insert(0, config.DATA_DEFAULT_SOURCE)
    return names


def _make_source(name: str):
    from deeppulse.src.sources import make_source

    return make_source(name)


def fetch_daily_kline_online(code: str, start: date, end: date, source_name: str = None) -> tuple[pd.DataFrame, str]:
    """在线拉日线。返回 (df, source_name)；df 列与库表 daily_kline 一致。

    按 DATA_DEFAULT_SOURCE 优先，失败自动降级另一源；全部失败抛 OnlineFetchError。
    """
    names = [source_name] if source_name else _source_names()
    key = ("daily", code, start.isoformat(), end.isoformat(), tuple(names))
    cached = _cache_get(key)
    if cached is not None:
        return cached

    errors = []
    for name in names:
        try:
            from deeppulse.src.collector import fetch_kline_with_timeout

            source = _make_source(name)
            df = fetch_kline_with_timeout(source, code, start, end, timeout=config.ONLINE_FETCH_TIMEOUT)
            if isinstance(df, pd.DataFrame) and not df.empty:
                result = (df.reset_index(drop=True), f"online-{name}")
                _cache_put(key, result)
                return result
            errors.append(f"{name}: 无数据")
        except Exception as e:  # noqa: BLE001 — 多源降级需要吞掉单源异常
            errors.append(f"{name}: {e}")
    raise OnlineFetchError(f"{_HINT}（{'；'.join(errors)}）")


def fetch_stock_list_online() -> list[dict]:
    """在线拉股票列表（字段同 upsert_stock_info 输入）。失败抛 OnlineFetchError。"""
    cached = _cache_get(("stock_list",))
    if cached is not None:
        return cached

    errors = []
    for name in _source_names():
        try:
            records = _make_source(name).fetch_stock_list()
            if records:
                _cache_put(("stock_list",), records)
                return records
            errors.append(f"{name}: 空列表")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
    raise OnlineFetchError(f"{_HINT}（{'；'.join(errors)}）")


def fetch_minute_kline_online(code: str, period: str, days: int) -> pd.DataFrame:
    """在线拉分钟线（复用 timeframes.fetch_minute_kline），带限频与会话缓存。"""
    key = ("minute", code, period, days)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        from deeppulse.agent.timeframes import fetch_minute_kline

        time.sleep(config.ONLINE_MINUTE_INTERVAL)  # akshare 分钟接口限频
        df = fetch_minute_kline(code, period=period, days=days)
        if isinstance(df, pd.DataFrame):
            _cache_put(key, df)
            return df
        return pd.DataFrame()
    except Exception as e:  # noqa: BLE001
        raise OnlineFetchError(f"{_HINT}（minute {period}: {e}）") from e


def fetch_weekly_kline_online(code: str, start: date, end: date) -> pd.DataFrame:
    """在线拉周线（复用 timeframes.fetch_weekly_kline），带会话缓存。"""
    key = ("weekly", code, start.isoformat(), end.isoformat())
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        from deeppulse.agent.timeframes import fetch_weekly_kline

        df = fetch_weekly_kline(code, start_date=str(start), end_date=str(end))
        if isinstance(df, pd.DataFrame):
            _cache_put(key, df)
            return df
        return pd.DataFrame()
    except Exception as e:  # noqa: BLE001
        raise OnlineFetchError(f"{_HINT}（weekly: {e}）") from e


def fetch_market_snapshot() -> pd.DataFrame:
    """全市场当日快照（ak.stock_zh_a_spot_em），复用东财源 30 秒缓存避免重复打接口。

    返回列含 代码/名称/最新价/涨跌幅/成交量/成交额/换手率/量比 等（东财口径）。
    """
    try:
        from deeppulse.src.realtime.eastmoney_source import EastMoneyRealtimeSource

        source = EastMoneyRealtimeSource()
        df = source._get_snapshot()  # noqa: SLF001 — 同包内复用其快照缓存
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
        raise OnlineFetchError(f"{_HINT}（eastmoney spot: 空快照）")
    except OnlineFetchError:
        raise
    except Exception as e:  # noqa: BLE001
        raise OnlineFetchError(f"{_HINT}（eastmoney spot: {e}）") from e


# ── 可选写回（仅 data.cache_to_db=True 时） ──────────────────────────────────────


def maybe_cache_kline(df: pd.DataFrame, db_path=None) -> int:
    """cache_to_db 开启时写回 daily_kline（不写 fetch_log——这是缓存不是采集）。返回写入行数。"""
    if not config.DATA_CACHE_TO_DB or df is None or df.empty:
        return 0
    from deeppulse.src.database import get_connection, init_tables, insert_daily_kline

    conn = get_connection(db_path)
    try:
        init_tables(conn)
        return insert_daily_kline(conn, df.to_dict("records"))
    finally:
        conn.close()


def maybe_cache_stock_list(records: list[dict], db_path=None) -> int:
    """cache_to_db 开启时写回 stock_info。返回写入行数。"""
    if not config.DATA_CACHE_TO_DB or not records:
        return 0
    from deeppulse.src.database import get_connection, init_tables, upsert_stock_info

    conn = get_connection(db_path)
    try:
        init_tables(conn)
        return upsert_stock_info(conn, records)
    finally:
        conn.close()


# ── 本地数据可用性（供批量工具选路径） ──────────────────────────────────────


def _table_has_rows(table: str, db_path=None) -> bool:
    try:
        import duckdb

        path = str(db_path or config.DB_PATH)
        conn = duckdb.connect(path, read_only=True)
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — 表缺/库缺/只读失败一律视为不可用
        return False


def local_kline_available(db_path=None) -> bool:
    """daily_kline 表存在且有行"""
    return _table_has_rows("daily_kline", db_path)


def local_stock_list_available(db_path=None) -> bool:
    """stock_info 表存在且有行"""
    return _table_has_rows("stock_info", db_path)


def session_cache_size() -> int:
    """当前会话缓存条目数（测试/调试用）"""
    return len(_cache)
