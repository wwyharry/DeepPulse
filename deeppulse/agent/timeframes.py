"""多周期K线数据 - 分钟级(1/5/15/30/60分钟)和周线数据采集与查询

入库口径：volume 统一为**股**（akshare/东财原始单位为手，取数端 ×100），
与日线表一致；工具输出层再按需股→手转换。
"""

import logging
import time
from datetime import date, timedelta

import duckdb
import pandas as pd

from deeppulse import config
from deeppulse.src.database import get_connection, init_timeframe_tables

logger = logging.getLogger(__name__)

# 支持的周期
TIMEFRAMES = {
    "1m": {"label": "1分钟", "akshare_period": "1", "db_table": "kline_1m"},
    "5m": {"label": "5分钟", "akshare_period": "5", "db_table": "kline_5m"},
    "15m": {"label": "15分钟", "akshare_period": "15", "db_table": "kline_15m"},
    "30m": {"label": "30分钟", "akshare_period": "30", "db_table": "kline_30m"},
    "60m": {"label": "60分钟", "akshare_period": "60", "db_table": "kline_60m"},
    "weekly": {"label": "周线", "akshare_period": "weekly", "db_table": "kline_weekly"},
}

# 批量采集默认周期集（1m 不含：trends2 仅 5 日且非 kline 通道）
BATCH_TIMEFRAMES = ["5m", "15m", "30m", "60m", "weekly"]


def fetch_minute_kline(code: str, period: str = "5", days: int = 5) -> pd.DataFrame:
    """从 AkShare 获取分钟K线数据

    Args:
        code: 股票代码
        period: 周期 "1"/"5"/"15"/"30"/"60"
        days: 获取天数（最近N个交易日的分钟数据）
    """
    import akshare as ak

    try:
        df = ak.stock_zh_a_hist_min_em(
            symbol=code,
            period=period,
            start_date=(date.today() - timedelta(days=days)).strftime("%Y-%m-%d 09:30:00"),
            end_date=date.today().strftime("%Y-%m-%d 15:00:00"),
            adjust="qfq",
        )

        if df is None or df.empty:
            return pd.DataFrame()

        # 统一列名
        col_map = {
            "时间": "trade_datetime",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        }
        df = df.rename(columns=col_map)
        df["code"] = code
        df["data_source"] = "akshare"
        # 成交量：东财口径为手，统一换算为股（与日线表一致）
        df["volume"] = (pd.to_numeric(df["volume"], errors="coerce") * 100).astype("Int64")

        cols = ["code", "trade_datetime", "open", "high", "low", "close", "volume", "amount", "data_source"]
        return df[[c for c in cols if c in df.columns]]
    except Exception as e:
        print(f"获取{code}分钟K线失败: {e}")
        return pd.DataFrame()


def fetch_weekly_kline(code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """从 AkShare 获取周线数据"""
    import akshare as ak

    try:
        if not start_date:
            start_date = (date.today() - timedelta(days=5 * 365)).strftime("%Y%m%d")
        if not end_date:
            end_date = date.today().strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="weekly",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )

        if df is None or df.empty:
            return pd.DataFrame()

        col_map = {
            "日期": "trade_datetime",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover",
        }
        df = df.rename(columns=col_map)
        df["code"] = code
        df["data_source"] = "akshare"
        df["trade_datetime"] = pd.to_datetime(df["trade_datetime"])
        # 成交量：东财口径为手，统一换算为股（与日线表一致）
        df["volume"] = (pd.to_numeric(df["volume"], errors="coerce") * 100).astype("Int64")

        cols = ["code", "trade_datetime", "open", "high", "low", "close", "volume", "amount", "data_source"]
        return df[[c for c in cols if c in df.columns]]
    except Exception as e:
        print(f"获取{code}周线失败: {e}")
        return pd.DataFrame()


def save_timeframe_data(conn, df: pd.DataFrame, table: str) -> int:
    """保存多周期K线数据到数据库"""
    if df.empty:
        return 0

    records = df.to_dict("records")
    count = 0
    for r in records:
        try:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {table}
                (code, trade_datetime, open, high, low, close, volume, amount, data_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    r["code"],
                    r["trade_datetime"],
                    r["open"],
                    r["high"],
                    r["low"],
                    r["close"],
                    r.get("volume"),
                    r.get("amount"),
                    r.get("data_source", "akshare"),
                ],
            )
            count += 1
        except Exception:
            continue
    return count


def update_timeframe(code: str, timeframe: str = "5m", days: int = 5, db_path=None) -> dict:
    """更新单只股票的指定周期K线数据

    Args:
        code: 股票代码
        timeframe: 周期（1m/5m/15m/30m/60m/weekly）
        days: 分钟数据天数 / 周线数据年数
        db_path: 数据库路径（显式下传；None 用 config.DB_PATH）
    """
    if timeframe not in TIMEFRAMES:
        return {"error": f"不支持的周期: {timeframe}，可选: {list(TIMEFRAMES.keys())}"}

    info = TIMEFRAMES[timeframe]
    conn = get_connection(db_path)
    init_timeframe_tables(conn)

    try:
        if timeframe == "weekly":
            df = fetch_weekly_kline(code)
        else:
            df = fetch_minute_kline(code, period=info["akshare_period"], days=days)

        if df.empty:
            return {"code": code, "timeframe": timeframe, "status": "no_data"}

        count = save_timeframe_data(conn, df, info["db_table"])
        conn.close()

        return {
            "code": code,
            "timeframe": timeframe,
            "label": info["label"],
            "rows": count,
            "status": "ok",
        }
    except Exception as e:
        conn.close()
        return {"code": code, "timeframe": timeframe, "error": str(e)}


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON 友好的记录列表（时间转 str、浮点保留 4 位）"""
    records = []
    for _, row in df.iterrows():
        r = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                r[k] = str(v)
            elif isinstance(v, float):
                r[k] = round(v, 4)
            elif hasattr(v, "item"):
                r[k] = v.item()
            else:
                r[k] = v
        records.append(r)
    return records


def _fallback_timeframe_online(code: str, timeframe: str, limit: int, db_path=None) -> tuple[pd.DataFrame, str]:
    """本地缺数时在线拉分钟/周线（默认不写库；cache_to_db 开启才落库）。"""
    from deeppulse.src.online import fetch_minute_kline_online, fetch_weekly_kline_online

    info = TIMEFRAMES[timeframe]
    if timeframe == "weekly":
        end = date.today()
        start = end - timedelta(days=max(30, int(limit) * 7 + 14))
        df = fetch_weekly_kline_online(code, start, end)
        if "trade_datetime" not in df.columns and "trade_date" in df.columns:
            df = df.rename(columns={"trade_date": "trade_datetime"})
    else:
        days = min(30, max(2, int(limit) // 20 + 2))
        df = fetch_minute_kline_online(code, info["akshare_period"], days)

    if df is not None and not df.empty:
        df = df.sort_values("trade_datetime").tail(int(limit)).reset_index(drop=True)
        if config.DATA_CACHE_TO_DB:
            save_timeframe_data(get_connection(db_path), df, info["db_table"])
    return (df if df is not None else pd.DataFrame()), "online-akshare"


def query_timeframe(code: str, timeframe: str = "5m", limit: int = 100, db_path=None) -> dict:
    """查询多周期K线数据。本地缺数时在线兜底（data_source 标注来源）。

    Args:
        code: 股票代码
        timeframe: 周期
        limit: 返回条数
        db_path: 数据库路径（显式下传；None 用 config.DB_PATH）
    """
    if timeframe not in TIMEFRAMES:
        return {"error": f"不支持的周期: {timeframe}"}

    table = TIMEFRAMES[timeframe]["db_table"]
    conn = get_connection(db_path)
    data_source = "local"

    try:
        try:
            df = conn.execute(
                f"""
                SELECT * FROM {table}
                WHERE code = ?
                ORDER BY trade_datetime DESC
                LIMIT ?
            """,
                [code, int(limit)],
            ).fetchdf()
        except duckdb.CatalogException:
            df = pd.DataFrame()  # 表未建（空库）→ 走在线兜底
        conn.close()

        if df.empty:
            if config.DATA_ONLINE_FALLBACK:
                df, data_source = _fallback_timeframe_online(code, timeframe, limit, db_path=db_path)
            if df.empty:
                return {
                    "code": code,
                    "timeframe": timeframe,
                    "count": 0,
                    "message": f"无{timeframe}数据，可在线获取（请检查网络）或用 update_timeframe 更新",
                }

        df = df.sort_values("trade_datetime")
        records = _df_to_records(df)

        return {
            "code": code,
            "timeframe": timeframe,
            "label": TIMEFRAMES[timeframe]["label"],
            "count": len(records),
            "data_source": data_source,
            "kline": records,
        }
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def get_multi_timeframe_data(code: str, timeframes: list[str] = None, limit: int = 50, db_path=None) -> dict:
    """获取多周期数据（用于多周期共振分析）

    Args:
        code: 股票代码
        timeframes: 周期列表，默认 ["daily", "60m", "15m"]
        limit: 每个周期返回条数
        db_path: 数据库路径（显式下传；None 用 config.DB_PATH）
    """
    if timeframes is None:
        timeframes = ["daily", "60m", "15m"]

    result = {"code": code, "timeframes": {}}

    for tf in timeframes:
        if tf == "daily":
            from deeppulse.src.query import StockQuery, result_source

            query = StockQuery(db_path)
            df = query.get_daily_kline(code, limit=limit)
            if not df.empty:
                result["timeframes"]["daily"] = {
                    "label": "日线",
                    "count": len(df),
                    "data_source": result_source(df),
                    "kline": _df_to_records(df),
                }
        elif tf in TIMEFRAMES:
            data = query_timeframe(code, tf, limit, db_path=db_path)
            result["timeframes"][tf] = data

    return result


def fetch_all_timeframe(
    timeframe: str = "5m", days: int = 5, codes: list[str] | None = None, db_path=None
) -> dict:
    """批量采集多周期K线到本地（东财直连 + 断点续传 + 节流 + 失败重试一次跳过）

    - 批量唯一路径走东财 kline 接口（服务端按窗口裁剪；akshare 分钟接口
      服务端拉全史再客户端切片，禁用于批量）
    - 断点续传：窗口内已有数据的代码跳过——重复运行只补缺口；刷新单只用
      update_timeframe，整表重采先清表
    - 节流：每只间隔 config.MINUTE_FETCH_DELAY_SECONDS（东财掐线时调大）
    - 失败重试一次，仍失败计入 failed 并继续下一只
    - 1m 不支持批量（trends2 仅 5 日且非 kline 通道）

    Args:
        timeframe: 周期（5m/15m/30m/60m/weekly）
        days: 采集窗口天数（默认最近 5 个自然日）
        codes: 指定股票列表（默认本地 stock_info 全部）
        db_path: 数据库路径（显式下传；None 用 config.DB_PATH）

    Returns:
        dict: total/success/skipped/failed/rows
    """
    if timeframe not in TIMEFRAMES:
        return {"error": f"不支持的周期: {timeframe}，可选: {list(TIMEFRAMES.keys())}"}
    if timeframe == "1m":
        return {"error": "1m 不支持批量采集（免费接口仅近5日且非 kline 通道），单只用 update_timeframe"}

    info = TIMEFRAMES[timeframe]
    table = info["db_table"]
    window_start = date.today() - timedelta(days=max(1, int(days)))
    end = date.today()

    from deeppulse.src.database import get_stock_list
    from deeppulse.src.sources.eastmoney_source import EastMoneySource

    conn = get_connection(db_path)
    init_timeframe_tables(conn)
    try:
        target_codes = list(codes) if codes is not None else get_stock_list(conn)
        if not target_codes:
            return {"total": 0, "message": "股票列表为空，请先 /fetch stocks"}

        src = EastMoneySource()
        stats = {"total": len(target_codes), "success": 0, "skipped": 0, "failed": 0, "rows": 0}
        try:
            for i, code in enumerate(target_codes):
                c6 = str(code).zfill(6)
                # 断点续传：窗口内已有数据的代码跳过
                existing = conn.execute(
                    f"SELECT 1 FROM {table} WHERE code = ? AND trade_datetime >= ? LIMIT 1",
                    [c6, pd.Timestamp(window_start)],
                ).fetchone()
                if existing:
                    stats["skipped"] += 1
                    continue

                df = None
                for attempt in (1, 2):
                    try:
                        df = src.fetch_minute_kline(c6, info["akshare_period"], window_start, end)
                        break
                    except NotImplementedError:
                        return {"error": f"东财 kline 通道不支持周期 {timeframe}"}
                    except Exception as e:  # noqa: BLE001 — 单只失败不中断批量
                        if attempt == 2:
                            logger.warning(f"  {c6} {timeframe} 采集失败: {e}")
                        else:
                            time.sleep(config.MINUTE_FETCH_DELAY_SECONDS * 2)

                if df is None or df.empty:
                    stats["failed"] += 1
                    continue

                stats["rows"] += save_timeframe_data(conn, df, table)
                stats["success"] += 1
                if (i + 1) % 100 == 0:
                    logger.info(f"  进度: {i + 1}/{len(target_codes)}")
                time.sleep(config.MINUTE_FETCH_DELAY_SECONDS)
        finally:
            src.cleanup()
        return stats
    finally:
        conn.close()
