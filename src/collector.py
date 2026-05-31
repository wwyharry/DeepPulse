"""数据采集协调器 - 多源采集、去重合并、断点续传"""
import time
import threading
from datetime import date, timedelta
import pandas as pd

import config
from src.database import (
    get_connection, init_tables, upsert_stock_info,
    insert_daily_kline, log_fetch, get_latest_kline_date, get_stock_list,
)
from src.sources import AkShareSource, BaoStockSource


# 数据源注册表
SOURCE_REGISTRY = {
    "akshare": AkShareSource,
    "baostock": BaoStockSource,
}


def get_sources() -> list:
    """获取配置中启用的数据源实例"""
    return [SOURCE_REGISTRY[name]() for name in config.DATA_SOURCES
            if name in SOURCE_REGISTRY]


def fetch_kline_with_timeout(source, code: str, start: date, end: date,
                             timeout: int = 30) -> pd.DataFrame:
    """带超时保护采集单只股票K线，避免数据源卡死整个更新任务。"""
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


def fetch_and_store_stock_list() -> int:
    """从各数据源采集股票列表并合并写入数据库"""
    conn = get_connection()
    init_tables(conn)

    all_stocks = {}  # code -> dict

    for source in get_sources():
        print(f"[{source.name}] 获取股票列表...")
        try:
            stocks = source.fetch_stock_list()
            print(f"  获取到 {len(stocks)} 只主板股票")
            for s in stocks:
                if s["code"] not in all_stocks:
                    all_stocks[s["code"]] = s
                else:
                    # 合并：保留有list_date的记录
                    existing = all_stocks[s["code"]]
                    if not existing.get("list_date") and s.get("list_date"):
                        existing["list_date"] = s["list_date"]
        except Exception as e:
            print(f"  [{source.name}] 获取失败: {e}")

    records = list(all_stocks.values())
    if not records:
        conn.close()
        raise RuntimeError("未获取到股票列表，保留现有 stock_info 不覆盖")

    conn.execute("DELETE FROM stock_info")
    count = upsert_stock_info(conn, records)
    print(f"\n共写入/更新 {count} 只股票信息")
    conn.close()
    return count


def fetch_kline_for_stock(code: str, source, start: date, end: date,
                          conn) -> int:
    """为单只股票采集日K数据"""
    # 断点续传：以真实K线最新交易日为准，避免空成功日志导致跳过缺口
    last_date = get_latest_kline_date(conn, code, source.name)
    actual_start = start
    if last_date:
        last = date.fromisoformat(last_date)
        if last >= end:
            return 0  # 已经采集完毕
        actual_start = last + timedelta(days=1)

    df = fetch_kline_with_timeout(source, code, actual_start, end)

    if df.empty:
        log_fetch(conn, code, source.name, actual_start, end, 0, "success")
        return 0

    records = df.to_dict("records")
    count = insert_daily_kline(conn, records)
    log_fetch(conn, code, source.name, actual_start, end, count, "success")
    return count


def fetch_all_kline(start_date: date = None, end_date: date = None,
                    source_names: list[str] = None, codes: list[str] = None) -> dict:
    """批量采集所有股票日K数据

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

    conn = get_connection()
    init_tables(conn)

    if codes is None:
        codes = get_stock_list(conn)
    if not codes:
        print("股票列表为空，请先运行 fetch_stocks.py")
        conn.close()
        return {"total": 0}

    sources_to_use = get_sources()
    if source_names:
        sources_to_use = [s for s in sources_to_use if s.name in source_names]

    stats = {"total": len(codes), "success": 0, "failed": 0, "rows": 0}

    for source in sources_to_use:
        print(f"\n=== 数据源: {source.name} ===")
        print(f"采集范围: {start} ~ {end}, 共 {len(codes)} 只股票")

        for i, code in enumerate(codes):
            try:
                count = fetch_kline_for_stock(code, source, start, end, conn)
                stats["rows"] += count
                stats["success"] += 1
                if (i + 1) % 100 == 0:
                    print(f"  进度: {i + 1}/{len(codes)}")
            except Exception as e:
                stats["failed"] += 1
                log_fetch(conn, code, source.name, start, end, 0, "failed", str(e))
                print(f"  [{source.name}] {code} 失败: {e}")

            time.sleep(config.FETCH_DELAY_SECONDS)

    conn.close()
    print(f"\n采集完成: 成功{stats['success']}, 失败{stats['failed']}, 写入{stats['rows']}行")
    return stats
