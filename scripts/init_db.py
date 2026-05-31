"""初始化数据库 - 创建表结构"""

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import config
from src.database import get_connection, init_tables


def main():
    print(f"初始化数据库: {config.DB_PATH}")
    conn = get_connection()
    init_tables(conn)
    print("表结构创建完成:")
    print("  - stock_info (股票基本信息)")
    print("  - daily_kline (日K线数据)")
    print("  - fetch_log (采集日志)")
    conn.close()


if __name__ == "__main__":
    main()
