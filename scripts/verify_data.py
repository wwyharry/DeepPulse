"""数据质量校验"""

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from deeppulse.src.database import get_connection
from deeppulse.src.query import StockQuery


def main():
    q = StockQuery()

    print("=" * 50)
    print("数据质量校验报告")
    print("=" * 50)

    # 1. 基本统计
    stats = q.get_data_stats()
    print("\n【数据概览】")
    print(f"  股票数量: {stats['stock_count']}")
    print(f"  K线总行数: {stats['kline_count']}")
    if stats["date_range"][0]:
        print(f"  日期范围: {stats['date_range'][0]} ~ {stats['date_range'][1]}")
    print(f"  数据源分布: {stats['sources']}")

    conn = get_connection()
    latest = stats["date_range"][1]
    if latest:
        coverage = conn.execute(
            """
            SELECT COUNT(DISTINCT code)
            FROM daily_kline
            WHERE trade_date = ?
        """,
            [latest],
        ).fetchone()[0]
        active_stocks = conn.execute(
            """
            SELECT COUNT(*)
            FROM stock_info
            WHERE delist_date IS NULL OR delist_date >= ?
        """,
            [latest],
        ).fetchone()[0]
        recent_counts = conn.execute("""
            SELECT trade_date, COUNT(DISTINCT code) AS stock_count
            FROM daily_kline
            GROUP BY trade_date
            ORDER BY trade_date DESC
            LIMIT 10
        """).fetchall()
        lagging = conn.execute(
            """
            SELECT s.code, s.name, MAX(k.trade_date) AS latest_date
            FROM stock_info s
            LEFT JOIN daily_kline k ON s.code = k.code
            WHERE s.delist_date IS NULL OR s.delist_date >= ?
            GROUP BY s.code, s.name
            HAVING MAX(k.trade_date) IS NULL OR MAX(k.trade_date) < ?
            ORDER BY latest_date NULLS FIRST, s.code
            LIMIT 20
        """,
            [latest, latest],
        ).fetchall()

        print("\n【最新交易日覆盖】")
        print(f"  最新交易日: {latest}")
        print(f"  当日有K线股票: {coverage}/{active_stocks}")
        print("  近10个交易日覆盖:")
        for trade_date, stock_count in recent_counts:
            print(f"    {trade_date}: {stock_count}只")
        if lagging:
            print("  滞后/缺失样例(最多20只):")
            for code, name, latest_date in lagging:
                print(f"    {code} {name}: {latest_date or '无K线'}")
        else:
            print("  无滞后股票")
    conn.close()

    # 2. 采集失败记录
    failed = q.get_fetch_log(status="failed", limit=10)
    if not failed.empty:
        print(f"\n【最近失败记录】({len(failed)}条)")
        print(failed.to_string(index=False))
    else:
        print("\n【采集失败记录】无")

    # 3. 抽样检查 - 取几只代表性股票
    sample_codes = ["600000", "000001", "600036", "000002"]
    print("\n【抽样检查】")
    for code in sample_codes:
        info = q.get_stock_info(code)
        if info.empty:
            print(f"  {code}: 未找到")
            continue
        name = info.iloc[0]["name"]
        kline = q.get_daily_kline(code)
        if kline.empty:
            print(f"  {code} {name}: 无K线数据")
        else:
            print(f"  {code} {name}: {len(kline)}条K线, {kline['trade_date'].min()} ~ {kline['trade_date'].max()}")


if __name__ == "__main__":
    main()
