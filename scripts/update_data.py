"""增量更新数据 - Agent启动时运行，只采集缺失的最近几天数据"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from deeppulse import config
from deeppulse.src.collector import fetch_and_store_stock_list, fetch_kline_with_timeout, get_sources
from deeppulse.src.database import (
    get_connection,
    get_latest_kline_date,
    get_stock_list,
    init_tables,
    insert_daily_kline,
    log_fetch,
)

RECONNECT_INTERVAL = 150
FETCH_TIMEOUT = 30


def check_db_status() -> dict:
    """检查数据库当前状态"""
    conn = get_connection()
    init_tables(conn)

    stock_count = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
    kline_count = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    latest = conn.execute("SELECT MAX(trade_date) FROM daily_kline").fetchone()[0]

    conn.close()
    return {
        "stock_count": stock_count,
        "kline_count": kline_count,
        "latest_date": str(latest) if latest else None,
    }


def update_stock_list() -> int:
    """更新股票列表（捕获新股/退市）"""
    return fetch_and_store_stock_list()


def update_kline(max_days: int = 5) -> dict:
    """增量更新K线数据，只采集最近缺失的天数

    Args:
        max_days: 最多向前回溯天数，默认5天（覆盖一个交易周）

    Returns:
        dict: 统计信息
    """
    end = date.today()
    # 只回溯max_days天，而不是5年
    start = end - timedelta(days=max_days)

    conn = get_connection()
    init_tables(conn)
    codes = get_stock_list(conn)

    if not codes:
        conn.close()
        return {"total": 0, "success": 0, "failed": 0, "rows": 0, "skipped": 0}

    # 用BaoStock（免费无限制）
    sources = [s for s in get_sources() if s.name == "baostock"]
    if not sources:
        conn.close()
        return {"error": "BaoStock数据源不可用"}

    source = sources[0]
    stats = {"total": len(codes), "success": 0, "failed": 0, "rows": 0, "skipped": 0}
    consecutive_failures = 0

    for i, code in enumerate(codes):
        if i > 0 and i % RECONNECT_INTERVAL == 0:
            try:
                source._logout()
            except Exception:
                pass
            time.sleep(1)
            source = sources[0].__class__()

        # 断点续传：检查真实K线最后交易日，而不是fetch_log请求结束日期
        last = get_latest_kline_date(conn, code, "baostock")
        if last:
            last_d = date.fromisoformat(last)
            if last_d >= end:
                stats["skipped"] += 1
                continue
            actual_start = last_d + timedelta(days=1)
        else:
            actual_start = start

        try:
            df = fetch_kline_with_timeout(source, code, actual_start, end, FETCH_TIMEOUT)
            if df is not None and not df.empty:
                records = df.to_dict("records")
                count = insert_daily_kline(conn, records)
                log_fetch(conn, code, "baostock", actual_start, end, count, "success")
                stats["rows"] += count
            else:
                log_fetch(conn, code, "baostock", actual_start, end, 0, "success")
            stats["success"] += 1
            consecutive_failures = 0
        except TimeoutError as e:
            stats["failed"] += 1
            consecutive_failures += 1
            log_fetch(conn, code, "baostock", actual_start, end, 0, "timeout", str(e))
            try:
                source._logout()
            except Exception:
                pass
            time.sleep(1)
            source = sources[0].__class__()
        except Exception as e:
            stats["failed"] += 1
            consecutive_failures += 1
            log_fetch(conn, code, "baostock", actual_start, end, 0, "failed", str(e))
            if consecutive_failures >= 3:
                try:
                    source._logout()
                except Exception:
                    pass
                time.sleep(2)
                source = sources[0].__class__()
                consecutive_failures = 0

        # 进度报告
        if (i + 1) % 200 == 0:
            print(f"  K线更新进度: {i + 1}/{len(codes)} (新增{stats['rows']}行, 跳过{stats['skipped']})", flush=True)

        time.sleep(config.FETCH_DELAY_SECONDS)

    try:
        source._logout()
    except Exception:
        pass
    conn.close()
    return stats


def run_update(skip_stock_list: bool = False, skip_kline: bool = False, max_days: int = 5, quiet: bool = False) -> dict:
    """执行增量更新，返回完整统计

    Args:
        skip_stock_list: 跳过股票列表更新
        skip_kline: 跳过K线数据更新
        max_days: K线回溯天数
        quiet: 安静模式，减少输出

    Returns:
        dict: 更新统计
    """
    result = {}

    # 1. 检查数据库状态
    if not quiet:
        print("[数据更新] 检查数据库状态...")
    status = check_db_status()
    result["db_before"] = status

    if not quiet:
        if status["stock_count"] == 0:
            print("  数据库为空，需要首次初始化")
        else:
            print(f"  股票: {status['stock_count']}只 | K线: {status['kline_count']}行 | 最新: {status['latest_date']}")

    # 2. 更新股票列表
    if not skip_stock_list:
        if not quiet:
            print("[数据更新] 更新股票列表...")
        try:
            stock_count = update_stock_list()
            result["stock_updated"] = stock_count
            if not quiet:
                print(f"  更新完成: {stock_count}只股票")
        except Exception as e:
            result["stock_error"] = str(e)
            if not quiet:
                print(f"  更新失败: {e}")

    # 3. 增量更新K线
    if not skip_kline:
        if not quiet:
            print(f"[数据更新] 增量更新K线数据 (最近{max_days}天)...")
        start_time = time.time()
        try:
            kline_stats = update_kline(max_days=max_days)
            elapsed = time.time() - start_time
            result["kline"] = kline_stats
            if not quiet:
                print(
                    f"  更新完成: 新增{kline_stats.get('rows', 0)}行 | "
                    f"跳过{kline_stats.get('skipped', 0)}只 | "
                    f"失败{kline_stats.get('failed', 0)}只 | "
                    f"耗时{elapsed:.0f}秒"
                )
        except Exception as e:
            result["kline_error"] = str(e)
            if not quiet:
                print(f"  更新失败: {e}")

    # 4. 更新后状态
    status_after = check_db_status()
    result["db_after"] = status_after
    if not quiet:
        print(
            f"[数据更新] 完成! 股票: {status_after['stock_count']}只 | "
            f"K线: {status_after['kline_count']}行 | "
            f"最新: {status_after['latest_date']}"
        )

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="增量更新A股数据（Agent启动时使用）")
    parser.add_argument("--skip-stocks", action="store_true", help="跳过股票列表更新")
    parser.add_argument("--skip-kline", action="store_true", help="跳过K线数据更新")
    parser.add_argument("--max-days", type=int, default=5, help="K线回溯天数，默认5")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    args = parser.parse_args()

    run_update(
        skip_stock_list=args.skip_stocks,
        skip_kline=args.skip_kline,
        max_days=args.max_days,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    main()
