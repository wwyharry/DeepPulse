"""批量采集日K线数据"""

import argparse
import logging
import sys
from datetime import date

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import config
from src.collector import fetch_all_kline

# 配置日志：实时输出到控制台
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)


def main():
    parser = argparse.ArgumentParser(description="采集A股日K线数据")
    parser.add_argument("--start", type=str, help="开始日期 YYYY-MM-DD", default=str(config.START_DATE))
    parser.add_argument("--end", type=str, help="结束日期 YYYY-MM-DD", default=str(config.END_DATE))
    parser.add_argument("--source", type=str, nargs="*", help="指定数据源", choices=["akshare", "baostock"])
    parser.add_argument("--codes", type=str, nargs="*", help="指定股票代码")
    parser.add_argument("--delay", type=float, help="每只股票间隔秒数（默认0，BaoStock无限流）", default=0)
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    # 临时覆盖采集间隔
    original_delay = config.FETCH_DELAY_SECONDS
    config.FETCH_DELAY_SECONDS = args.delay

    print("=" * 50)
    print("A股日K线数据采集")
    print(f"日期范围: {start} ~ {end}")
    print(f"数据源: {args.source or '全部'}")
    print(f"股票: {args.codes or '全部'}")
    print(f"采集间隔: {args.delay}秒")
    print("=" * 50)

    stats = fetch_all_kline(
        start_date=start,
        end_date=end,
        source_names=args.source,
        codes=args.codes,
    )
    config.FETCH_DELAY_SECONDS = original_delay
    print(f"\n采集统计: {stats}")


if __name__ == "__main__":
    main()
