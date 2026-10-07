"""采集沪深京A股列表（含创业板/科创板/北交所）"""

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from deeppulse.src.collector import fetch_and_store_stock_list


def main():
    print("=" * 40)
    print("采集沪深京A股列表（含创业板/科创板/北交所）")
    print("=" * 40)
    count = fetch_and_store_stock_list()
    print(f"\n完成，共 {count} 只股票")


if __name__ == "__main__":
    main()
