"""数据采集协调器 - 多源采集、去重合并、断点续传、韧性保护"""

import logging
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta

import pandas as pd

from deeppulse import config
from deeppulse.src.database import (
    get_connection,
    get_earliest_kline_date,
    get_latest_kline_date,
    get_stock_list,
    init_tables,
    insert_daily_kline,
    log_fetch,
    upsert_stock_info,
)
from deeppulse.src.sources import SOURCE_CLASSES
from deeppulse.src.validation import validate_kline_df

logger = logging.getLogger(__name__)

# 数据源注册表
SOURCE_REGISTRY: dict[str, type] = SOURCE_CLASSES


def get_sources() -> list:
    """按 config.DATA_SOURCES 优先级返回启用的数据源实例"""
    return [SOURCE_REGISTRY[name]() for name in config.DATA_SOURCES if name in SOURCE_REGISTRY]


def fetch_kline_with_timeout(source, code: str, start: date, end: date, timeout: int = None) -> pd.DataFrame:
    """带超时保护采集单只股票K线，避免数据源卡死整个更新任务。"""
    if timeout is None:
        timeout = config.FETCH_TIMEOUT
    result = [None]
    error = [None]

    def _fetch():
        try:
            result[0] = source.fetch_daily_kline(code, start, end)
        except Exception as e:
            error[0] = e

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        raise TimeoutError(f"采集{code}超时({timeout}秒)")
    if error[0]:
        raise error[0]
    return result[0] if result[0] is not None else pd.DataFrame()


class SourceFailover:
    """按优先级维护活动数据源的多源故障切换器（空结果/异常都换下一源）

    - 活动源返回数据 → 记住该源，后续优先用它（在线源新鲜度可能优于配置首选）
    - 连续 QUIET_EMPTY_STREAK 只代码全部源皆空 → 进入安静模式：只查活动源，
      避免全市场无新数据（周末/节假日/已是最新）时把所有源都打一遍
    - 单源异常 → 清理该源会话后换下一源；只有全部源都异常才抛 RuntimeError
      （「全部源都说没数据」不抛，返回空 DataFrame——那是有效答案）
    """

    QUIET_EMPTY_STREAK = 3

    def __init__(self, sources: list = None, timeout: int = None):
        self._sources = list(sources) if sources is not None else get_sources()
        if not self._sources:
            raise ValueError("无可用数据源（检查 config.DATA_SOURCES）")
        self._timeout = timeout
        self.active_index = 0
        self._all_empty_streak = 0
        self._quiet = False

    @property
    def active(self):
        """当前活动数据源（失败切换中会变化）"""
        return self._sources[self.active_index]

    @property
    def source_names(self) -> list[str]:
        return [s.name for s in self._sources]

    def fetch(self, code: str, start: date, end: date) -> tuple[pd.DataFrame, str]:
        """取日K，自动故障切换。返回 (df, source_name)

        Raises:
            RuntimeError: 所有数据源均异常失败（区别于「无数据」）
        """
        errors = []
        saw_empty = False
        n = len(self._sources)
        for step in range(n):
            if self._quiet and step > 0:
                break  # 安静模式只试活动源
            idx = (self.active_index + step) % n
            source = self._sources[idx]
            try:
                df = fetch_kline_with_timeout(source, code, start, end, self._timeout)
            except Exception as e:  # noqa: BLE001 — 多源切换需要吞掉单源异常
                errors.append(f"[{source.name}] {e}")
                self._release(source)
                self._quiet = False  # 活动源异常：退出安静模式继续换源
                continue
            if df is not None and not df.empty:
                self.active_index = idx
                self._all_empty_streak = 0
                self._quiet = False
                return df, source.name
            saw_empty = True
            errors.append(f"[{source.name}] 无数据")
            if self._quiet:
                break

        if saw_empty:
            self._all_empty_streak += 1
            if self._all_empty_streak >= self.QUIET_EMPTY_STREAK:
                self._quiet = True
            return pd.DataFrame(), self.active.name
        raise RuntimeError("所有数据源均失败: " + "；".join(errors))

    def fetch_index(self, code: str, start: date, end: date) -> tuple[pd.DataFrame, str]:
        """取指数日K，自动故障切换。返回 (df, source_name)

        与 fetch 的差别：NotImplementedError（源声明不支持指数）视作跳过而非失败；
        全部源不支持/无数据返回空 DataFrame，全部异常才抛 RuntimeError。
        """
        errors = []
        saw_empty = False
        n = len(self._sources)
        for step in range(n):
            idx = (self.active_index + step) % n
            source = self._sources[idx]
            try:
                df = source.fetch_index_kline(code, start, end)
            except NotImplementedError as e:  # noqa: BLE001 — 不支持指数=跳过该源（视同无数据）
                errors.append(f"[{source.name}] {e}")
                saw_empty = True
                continue
            except Exception as e:  # noqa: BLE001 — 多源切换需要吞掉单源异常
                errors.append(f"[{source.name}] {e}")
                self._release(source)
                continue
            if df is not None and not df.empty:
                self.active_index = idx
                return df, source.name
            saw_empty = True
            errors.append(f"[{source.name}] 无数据")

        if saw_empty:
            return pd.DataFrame(), self.active.name
        raise RuntimeError("所有数据源均失败: " + "；".join(errors))

    def cleanup_active(self):
        """清理活动数据源会话（批量任务的周期性重连用）"""
        self._release(self.active)

    def cleanup(self):
        """清理全部数据源资源"""
        for source in self._sources:
            self._release(source)

    @staticmethod
    def _release(source):
        """释放数据源会话：优先有界清理（BaoStock 残留线程在会话上时跳过 logout）"""
        try_cleanup = getattr(source, "try_cleanup", None)
        if callable(try_cleanup):
            try_cleanup()
        else:
            source.cleanup()


@contextmanager
def open_connection():
    """数据库连接上下文管理器，确保连接正确关闭"""
    conn = get_connection()
    init_tables(conn)
    try:
        yield conn
    finally:
        conn.close()


def fetch_and_store_stock_list() -> int:
    """从各数据源采集股票列表并合并写入数据库（带事务保护）"""
    with open_connection() as conn:
        all_stocks = {}  # code -> dict

        for source in get_sources():
            logger.info(f"[{source.name}] 获取股票列表...")
            try:
                stocks = source.fetch_stock_list()
                logger.info(f"  获取到 {len(stocks)} 只A股")
                for s in stocks:
                    if s["code"] not in all_stocks:
                        all_stocks[s["code"]] = s
                    else:
                        # 合并：保留有list_date的记录
                        existing = all_stocks[s["code"]]
                        if not existing.get("list_date") and s.get("list_date"):
                            existing["list_date"] = s["list_date"]
            except Exception as e:
                logger.warning(f"  [{source.name}] 获取失败: {e}")

        records = list(all_stocks.values())
        if not records:
            raise RuntimeError("未获取到股票列表，保留现有 stock_info 不覆盖")

        count = upsert_stock_info(conn, records)
        logger.info(f"\n共写入/更新 {count} 只股票信息")
        return count


def _split_resume_segments(
    first_date: str | None, last_date: str | None, start: date, end: date
) -> list[tuple[date, date]]:
    """按已有覆盖端点把 [start, end] 切成至多两段（前缀补历史 + 后缀增量）

    断点续传不只看 MAX(trade_date)：库中已有近期数据时，调用方请求更早
    区间补历史不能被静默跳过。按已有数据的 [min, max] 端点切段——前缀补
    历史 (start, min-1) 与后缀增量 (max(start, last+1), end)。
    注意：MIN/MAX 分段不会填补 [min, max] 内部的空洞。
    """
    if not first_date or not last_date:
        return [(start, end)]
    first = date.fromisoformat(first_date)
    last = date.fromisoformat(last_date)
    segments: list[tuple[date, date]] = []
    if start < first:
        segments.append((start, first - timedelta(days=1)))
    if last < end:
        segments.append((max(start, last + timedelta(days=1)), end))
    return segments


def fetch_kline_for_stock(
    code: str, failover: SourceFailover, start: date, end: date, conn, cover_all_sources: bool = True
) -> int:
    """为单只股票采集日K数据（带验证、按端点覆盖分段续传，经多源故障切换取数）

    cover_all_sources=True（多源互备）：续传端点取全部源的合并覆盖——任一源
    有数即算已覆盖，只补本地真正缺失的日期（数据源可互换，不重复存副本）；
    False（显式单源采集）：按该源自身覆盖续传，允许整段源内副本。
    """
    scope = None if cover_all_sources else failover.active.name
    first_date = get_earliest_kline_date(conn, code, scope)
    last_date = get_latest_kline_date(conn, code, scope)

    segments = _split_resume_segments(first_date, last_date, start, end)
    if not segments:
        return 0  # 请求区间已被已有数据完整覆盖

    total = 0
    for seg_start, seg_end in segments:
        df, src_name = failover.fetch(code, seg_start, seg_end)

        # 数据验证
        if not df.empty and config.DATA_VALIDATION_ENABLED:
            df = validate_kline_df(df, code)

        if df.empty:
            log_fetch(conn, code, failover.active.name, seg_start, seg_end, 0, "success")
            continue

        records = df.to_dict("records")
        count = insert_daily_kline(conn, records)
        log_fetch(conn, code, src_name, seg_start, seg_end, count, "success")
        total += count
    return total


def fetch_index_for_code(code: str, failover: SourceFailover, start: date, end: date, conn) -> int:
    """为单只指数采集日K数据（断点分段续用、多源故障切换），返回新增行数

    code 为带前缀全码（sh000001 等）；入库 index_daily_kline 表。
    """
    first_date = get_earliest_kline_date(conn, code, table="index_daily_kline")
    last_date = get_latest_kline_date(conn, code, table="index_daily_kline")

    segments = _split_resume_segments(first_date, last_date, start, end)
    if not segments:
        return 0  # 请求区间已被已有数据完整覆盖

    total = 0
    for seg_start, seg_end in segments:
        df, src_name = failover.fetch_index(code, seg_start, seg_end)

        if not df.empty and config.DATA_VALIDATION_ENABLED:
            df = validate_kline_df(df, code)

        if df.empty:
            log_fetch(conn, code, failover.active.name, seg_start, seg_end, 0, "success")
            continue

        count = insert_daily_kline(conn, df.to_dict("records"), table="index_daily_kline")
        log_fetch(conn, code, src_name, seg_start, seg_end, count, "success")
        total += count
    return total


def fetch_all_index_kline(start_date: date = None, end_date: date = None) -> dict:
    """批量采集指数日K（config.INDEX_CODES 全部，约6只、秒级）"""
    start = start_date or config.START_DATE
    end = end_date or config.END_DATE

    with open_connection() as conn:
        failover = SourceFailover()
        logger.info(f"采集指数: {' / '.join(f'{c}({n})' for c, n in config.INDEX_CODES.items())}")
        stats = {"total": len(config.INDEX_CODES), "success": 0, "failed": 0, "rows": 0}
        for code in config.INDEX_CODES:
            try:
                count = fetch_index_for_code(code, failover, start, end, conn)
                stats["rows"] += count
                stats["success"] += 1
            except Exception as e:
                stats["failed"] += 1
                log_fetch(conn, code, failover.active.name, start, end, 0, "failed", str(e))
                logger.warning(f"  [{failover.active.name}] 指数 {code} 失败: {e}")
        return stats


def fetch_all_kline(
    start_date: date = None,
    end_date: date = None,
    source_names: list[str] = None,
    codes: list[str] = None,
) -> dict:
    """批量采集所有股票日K数据（多源故障切换、周期性重连、连续失败检测、数据验证）

    多源语义：数据源互为备份——按优先级取数，单源失败/无数据自动换下一源，
    任一源补齐该代码的缺口即算完成（不为每个源各存一份副本）。显式指定单一
    source_names 时按该源自身覆盖续传（可整段采集指定源的副本）。

    Args:
        start_date: 开始日期（默认配置中的5年前）
        end_date: 结束日期（默认今天）
        source_names: 指定数据源（默认全部，按 config.DATA_SOURCES 优先级）
        codes: 指定股票代码列表（默认全部）

    Returns:
        dict: 统计信息
    """
    start = start_date or config.START_DATE
    end = end_date or config.END_DATE

    with open_connection() as conn:
        if codes is None:
            codes = get_stock_list(conn)
        if not codes:
            logger.warning("股票列表为空，请先运行 fetch_stocks.py")
            return {"total": 0}

        sources_to_use = get_sources()
        if source_names:
            sources_to_use = [s for s in sources_to_use if s.name in source_names]
        if not sources_to_use:
            raise ValueError(f"无可用数据源: {source_names or config.DATA_SOURCES}")

        failover = SourceFailover(sources_to_use)
        cover_all_sources = len(sources_to_use) > 1
        logger.info(f"数据源: {' / '.join(failover.source_names)}")
        logger.info(f"采集范围: {start} ~ {end}, 共 {len(codes)} 只股票")

        stats = {"total": len(codes), "success": 0, "failed": 0, "rows": 0}

        consecutive_failures = 0
        last_reconnect_time = time.time()
        RECONNECT_TIME_INTERVAL = 40  # 每40秒主动重连（BaoStock session约50秒超时）

        for i, code in enumerate(codes):
            # 周期性重连：按数量 或 按时间，先到先触发
            time_since_reconnect = time.time() - last_reconnect_time
            should_reconnect = (
                i > 0 and i % config.FETCH_RECONNECT_INTERVAL == 0
            ) or time_since_reconnect > RECONNECT_TIME_INTERVAL
            if should_reconnect:
                logger.info(
                    f"--- 定期重连 {failover.active.name} (第{i}只, 距上次重连{time_since_reconnect:.0f}秒) ---"
                )
                failover.cleanup_active()
                time.sleep(1)
                last_reconnect_time = time.time()

            try:
                count = fetch_kline_for_stock(code, failover, start, end, conn, cover_all_sources=cover_all_sources)
                stats["rows"] += count
                stats["success"] += 1
                consecutive_failures = 0
                if (i + 1) % 100 == 0:
                    logger.info(f"  进度: {i + 1}/{len(codes)}")
            except Exception as e:
                stats["failed"] += 1
                consecutive_failures += 1
                log_fetch(conn, code, failover.active.name, start, end, 0, "failed", str(e))
                logger.warning(f"  [{failover.active.name}] {code} 失败: {e}")

                # 连续失败达阈值，尝试重连
                if consecutive_failures >= config.FETCH_CONSECUTIVE_FAIL_THRESHOLD:
                    logger.info(f"  连续失败{consecutive_failures}次，重连...")
                    failover.cleanup_active()
                    time.sleep(3)
                    consecutive_failures = 0

            time.sleep(config.FETCH_DELAY_SECONDS)

        # 采集完毕，清理全部数据源资源
        failover.cleanup()

        logger.info(f"\n采集完成: 成功{stats['success']}, 失败{stats['failed']}, 写入{stats['rows']}行")
        return stats
