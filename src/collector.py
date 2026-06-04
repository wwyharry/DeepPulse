"""数据采集协调器 - 多源采集、去重合并、断点续传、韧性保护"""

import logging
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta

import pandas as pd

import config
from src.database import (
    get_connection,
    get_latest_kline_date,
    get_stock_list,
    init_tables,
    insert_daily_kline,
    log_fetch,
    upsert_stock_info,
)
from src.sources import AkShareSource, BaoStockSource
from src.validation import validate_kline_df

logger = logging.getLogger(__name__)

# 数据源注册表
SOURCE_REGISTRY = {
    "akshare": AkShareSource,
    "baostock": BaoStockSource,
}


def get_sources() -> list:
    """获取配置中启用的数据源实例"""
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
                logger.info(f"  获取到 {len(stocks)} 只主板股票")
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


def fetch_kline_for_stock(code: str, source, start: date, end: date, conn) -> int:
    """为单只股票采集日K数据（带验证）"""
    # 断点续传：以真实K线最新交易日为准，避免空成功日志导致跳过缺口
    last_date = get_latest_kline_date(conn, code, source.name)
    actual_start = start
    if last_date:
        last = date.fromisoformat(last_date)
        if last >= end:
            return 0  # 已经采集完毕
        actual_start = last + timedelta(days=1)

    df = fetch_kline_with_timeout(source, code, actual_start, end)

    # 数据验证
    if not df.empty and config.DATA_VALIDATION_ENABLED:
        df = validate_kline_df(df, code)

    if df.empty:
        log_fetch(conn, code, source.name, actual_start, end, 0, "success")
        return 0

    records = df.to_dict("records")
    count = insert_daily_kline(conn, records)
    log_fetch(conn, code, source.name, actual_start, end, count, "success")
    return count


def fetch_all_kline(
    start_date: date = None,
    end_date: date = None,
    source_names: list[str] = None,
    codes: list[str] = None,
) -> dict:
    """批量采集所有股票日K数据（带周期性重连、连续失败检测、数据验证）

    Args:
        start_date: 开始日期（默认配置中的5年前）
        end_date: 结束日期（默认今天）
        source_names: 指定数据源（默认全部）
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

        stats = {"total": len(codes), "success": 0, "failed": 0, "rows": 0}

        for source in sources_to_use:
            logger.info(f"\n=== 数据源: {source.name} ===")
            logger.info(f"采集范围: {start} ~ {end}, 共 {len(codes)} 只股票")

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
                    logger.info(f"--- 定期重连 {source.name} (第{i}只, 距上次重连{time_since_reconnect:.0f}秒) ---")
                    source.cleanup()
                    time.sleep(1)
                    last_reconnect_time = time.time()

                try:
                    count = fetch_kline_for_stock(code, source, start, end, conn)
                    stats["rows"] += count
                    stats["success"] += 1
                    consecutive_failures = 0
                    if (i + 1) % 100 == 0:
                        logger.info(f"  进度: {i + 1}/{len(codes)}")
                except TimeoutError as e:
                    stats["failed"] += 1
                    consecutive_failures += 1
                    log_fetch(conn, code, source.name, start, end, 0, "timeout", str(e))
                    logger.warning(f"  [{source.name}] {code} 超时: {e}")
                    # 超时后立即重连
                    source.cleanup()
                    time.sleep(2)
                except (ConnectionError, ConnectionResetError, OSError) as e:
                    stats["failed"] += 1
                    consecutive_failures += 1
                    log_fetch(conn, code, source.name, start, end, 0, "failed", str(e))
                    logger.warning(f"  [{source.name}] {code} 连接异常: {e}")
                    # 连接类错误立即重连
                    logger.info("  连接异常，立即重连...")
                    source.cleanup()
                    time.sleep(3)
                    consecutive_failures = 0
                except Exception as e:
                    stats["failed"] += 1
                    consecutive_failures += 1
                    log_fetch(conn, code, source.name, start, end, 0, "failed", str(e))
                    logger.warning(f"  [{source.name}] {code} 失败: {e}")

                    # 连续失败达阈值，尝试重连
                    if consecutive_failures >= config.FETCH_CONSECUTIVE_FAIL_THRESHOLD:
                        logger.info(f"  连续失败{consecutive_failures}次，重连...")
                        source.cleanup()
                        time.sleep(3)
                        consecutive_failures = 0

                time.sleep(config.FETCH_DELAY_SECONDS)

            # 数据源采集完毕，清理资源
            source.cleanup()

        logger.info(f"\n采集完成: 成功{stats['success']}, 失败{stats['failed']}, 写入{stats['rows']}行")
        return stats
