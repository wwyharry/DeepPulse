"""全量日K数据采集脚本 - 带进度日志、定期重连、超时保护"""
import sys
import time
import threading
from datetime import date, timedelta
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import config
from src.database import get_connection, init_tables, get_stock_list, get_latest_kline_date, insert_daily_kline, log_fetch
from src.sources import BaoStockSource

LOG_FILE = config.PROJECT_ROOT / "fetch_progress.log"
RECONNECT_INTERVAL = 150  # 每150只股票重连一次
FETCH_TIMEOUT = 30        # 每只股票最大采集时间（秒）


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def create_source():
    """创建并登录BaoStock数据源"""
    source = BaoStockSource()
    source._ensure_login()
    return source


def fetch_with_timeout(source, code, start, end, timeout=FETCH_TIMEOUT):
    """带超时的数据采集"""
    result = [None]
    error = [None]

    def _fetch():
        try:
            result[0] = source.fetch_daily_kline(code, start, end)
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        # 超时了
        raise TimeoutError(f"采集{code}超时({timeout}秒)")
    if error[0]:
        raise error[0]
    return result[0]


def main():
    start_date = config.START_DATE
    end_date = config.END_DATE

    log("=" * 60)
    log(f"全量日K采集启动")
    log(f"日期范围: {start_date} ~ {end_date}")
    log(f"数据源: BaoStock | 超时: {FETCH_TIMEOUT}秒 | 重连间隔: {RECONNECT_INTERVAL}")
    log("=" * 60)

    conn = get_connection()
    init_tables(conn)
    codes = get_stock_list(conn)
    log(f"股票总数: {len(codes)}")

    source = create_source()
    total_success = 0
    total_failed = 0
    total_rows = 0
    skipped = 0
    start_time = time.time()
    consecutive_failures = 0

    for i, code in enumerate(codes):
        # 定期重连BaoStock
        if i > 0 and i % RECONNECT_INTERVAL == 0:
            log(f"--- 定期重连BaoStock (第{i}只) ---")
            try:
                source._logout()
            except Exception:
                pass
            time.sleep(2)
            source = create_source()

        # 断点续传检查：以真实K线最新交易日为准
        last = get_latest_kline_date(conn, code, "baostock")
        if last:
            last_d = date.fromisoformat(last)
            if last_d >= end_date:
                skipped += 1
                total_success += 1
                continue
            actual_start = last_d + timedelta(days=1)
        else:
            actual_start = start_date

        try:
            df = fetch_with_timeout(source, code, actual_start, end_date)
            if df is None or df.empty:
                log_fetch(conn, code, "baostock", actual_start, end_date, 0, "success")
                total_success += 1
            else:
                records = df.to_dict("records")
                count = insert_daily_kline(conn, records)
                log_fetch(conn, code, "baostock", actual_start, end_date, count, "success")
                total_success += 1
                total_rows += count
            consecutive_failures = 0
        except TimeoutError as e:
            total_failed += 1
            consecutive_failures += 1
            log_fetch(conn, code, "baostock", actual_start, end_date, 0, "timeout", str(e))
            log(f"  TIMEOUT {code}: {e}")
            # 超时后重连
            try:
                source._logout()
            except Exception:
                pass
            time.sleep(2)
            source = create_source()
        except Exception as e:
            total_failed += 1
            consecutive_failures += 1
            log_fetch(conn, code, "baostock", actual_start, end_date, 0, "failed", str(e))
            log(f"  FAIL {code}: {e}")

            # 连续失败3次，尝试重连
            if consecutive_failures >= 3:
                log(f"  连续失败{consecutive_failures}次，重连...")
                try:
                    source._logout()
                except Exception:
                    pass
                time.sleep(3)
                source = create_source()
                consecutive_failures = 0

        # 进度报告（每100只报告一次）
        if (i + 1) % 100 == 0:
            elapsed = time.time() - start_time
            processed = i + 1 - skipped
            rate = processed / elapsed if elapsed > 0 and processed > 0 else 0
            remaining_new = (len(codes) - i - 1) - (len(codes) - total_success - total_failed - skipped)
            remaining_time = remaining_new / rate if rate > 0 else 0
            log(f"进度: {i + 1}/{len(codes)} | 成功:{total_success} 失败:{total_failed} "
                f"跳过:{skipped} 行数:{total_rows} | 已用:{elapsed/60:.0f}分 "
                f"剩余:{remaining_time/60:.0f}分")

        time.sleep(config.FETCH_DELAY_SECONDS)

    elapsed = time.time() - start_time
    log("=" * 60)
    log(f"采集完成! 耗时: {elapsed/60:.1f}分钟")
    log(f"成功: {total_success}, 失败: {total_failed}, 跳过: {skipped}, 总行数: {total_rows}")
    log("=" * 60)

    try:
        source._logout()
    except Exception:
        pass
    conn.close()


if __name__ == "__main__":
    main()
