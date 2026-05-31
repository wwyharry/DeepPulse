"""采集沪深主板股票列表"""

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from src.collector import fetch_and_store_stock_list


def main():
    print("=" * 40)
    print("采集沪深主板股票列表")
    print("=" * 40)
    count = fetch_and_store_stock_list()
    print(f"\n完成，共 {count} 只股票")


if __name__ == "__main__":
    main()
