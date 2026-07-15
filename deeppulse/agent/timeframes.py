"""多周期K线数据 - 分钟级(1/5/15/30/60分钟)和周线数据采集与查询"""

from datetime import date, timedelta

import pandas as pd

from deeppulse.src.database import get_connection

# 支持的周期
TIMEFRAMES = {
    "1m": {"label": "1分钟", "akshare_period": "1", "db_table": "kline_1m"},
    "5m": {"label": "5分钟", "akshare_period": "5", "db_table": "kline_5m"},
    "15m": {"label": "15分钟", "akshare_period": "15", "db_table": "kline_15m"},
    "30m": {"label": "30分钟", "akshare_period": "30", "db_table": "kline_30m"},
    "60m": {"label": "60分钟", "akshare_period": "60", "db_table": "kline_60m"},
    "weekly": {"label": "周线", "akshare_period": "weekly", "db_table": "kline_weekly"},
}


def init_timeframe_tables(conn) -> None:
    """初始化多周期K线表"""
    for _tf, info in TIMEFRAMES.items():
        table = info["db_table"]
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                code VARCHAR NOT NULL,
                trade_datetime TIMESTAMP NOT NULL,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                amount DOUBLE,
                data_source VARCHAR DEFAULT 'akshare',
                PRIMARY KEY (code, trade_datetime)
            )
        """)


def fetch_minute_kline(code: str, period: str = "5", days: int = 5) -> pd.DataFrame:
    """从 AkShare 获取分钟K线数据

    Args:
        code: 股票代码
        period: 周期 "1"/"5"/"15"/"30"/"60"
        days: 获取天数（最近N个交易日的分钟数据）
    """
    import akshare as ak

    try:
        df = ak.stock_zh_a_hist_min_em(
            symbol=code,
            period=period,
            start_date=(date.today() - timedelta(days=days)).strftime("%Y-%m-%d 09:30:00"),
            end_date=date.today().strftime("%Y-%m-%d 15:00:00"),
            adjust="qfq",
        )

        if df is None or df.empty:
            return pd.DataFrame()

        # 统一列名
        col_map = {
            "时间": "trade_datetime",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        }
        df = df.rename(columns=col_map)
        df["code"] = code
        df["data_source"] = "akshare"

        cols = ["code", "trade_datetime", "open", "high", "low", "close", "volume", "amount", "data_source"]
        return df[[c for c in cols if c in df.columns]]
    except Exception as e:
        print(f"获取{code}分钟K线失败: {e}")
        return pd.DataFrame()


def fetch_weekly_kline(code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """从 AkShare 获取周线数据"""
    import akshare as ak

    try:
        if not start_date:
            start_date = (date.today() - timedelta(days=5 * 365)).strftime("%Y%m%d")
        if not end_date:
            end_date = date.today().strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="weekly",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )

        if df is None or df.empty:
            return pd.DataFrame()

        col_map = {
            "日期": "trade_datetime",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover",
        }
        df = df.rename(columns=col_map)
        df["code"] = code
        df["data_source"] = "akshare"
        df["trade_datetime"] = pd.to_datetime(df["trade_datetime"])

        cols = ["code", "trade_datetime", "open", "high", "low", "close", "volume", "amount", "data_source"]
        return df[[c for c in cols if c in df.columns]]
    except Exception as e:
        print(f"获取{code}周线失败: {e}")
        return pd.DataFrame()


def save_timeframe_data(conn, df: pd.DataFrame, table: str) -> int:
    """保存多周期K线数据到数据库"""
    if df.empty:
        return 0

    records = df.to_dict("records")
    count = 0
    for r in records:
        try:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {table}
                (code, trade_datetime, open, high, low, close, volume, amount, data_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    r["code"],
                    r["trade_datetime"],
                    r["open"],
                    r["high"],
                    r["low"],
                    r["close"],
                    r.get("volume"),
                    r.get("amount"),
                    r.get("data_source", "akshare"),
                ],
            )
            count += 1
        except Exception:
            continue
    return count


def update_timeframe(code: str, timeframe: str = "5m", days: int = 5) -> dict:
    """更新单只股票的指定周期K线数据

    Args:
        code: 股票代码
        timeframe: 周期（1m/5m/15m/30m/60m/weekly）
        days: 分钟数据天数 / 周线数据年数
    """
    if timeframe not in TIMEFRAMES:
        return {"error": f"不支持的周期: {timeframe}，可选: {list(TIMEFRAMES.keys())}"}

    info = TIMEFRAMES[timeframe]
    conn = get_connection()
    init_timeframe_tables(conn)

    try:
        if timeframe == "weekly":
            df = fetch_weekly_kline(code)
        else:
            df = fetch_minute_kline(code, period=info["akshare_period"], days=days)

        if df.empty:
            return {"code": code, "timeframe": timeframe, "status": "no_data"}

        count = save_timeframe_data(conn, df, info["db_table"])
        conn.close()

        return {
            "code": code,
            "timeframe": timeframe,
            "label": info["label"],
            "rows": count,
            "status": "ok",
        }
    except Exception as e:
        conn.close()
        return {"code": code, "timeframe": timeframe, "error": str(e)}


def query_timeframe(code: str, timeframe: str = "5m", limit: int = 100) -> dict:
    """查询多周期K线数据

    Args:
        code: 股票代码
        timeframe: 周期
        limit: 返回条数
    """
    if timeframe not in TIMEFRAMES:
        return {"error": f"不支持的周期: {timeframe}"}

    table = TIMEFRAMES[timeframe]["db_table"]
    conn = get_connection()

    try:
        df = conn.execute(
            f"""
            SELECT * FROM {table}
            WHERE code = ?
            ORDER BY trade_datetime DESC
            LIMIT ?
        """,
            [code, int(limit)],
        ).fetchdf()
        conn.close()

        if df.empty:
            return {
                "code": code,
                "timeframe": timeframe,
                "count": 0,
                "message": f"无{timeframe}数据，请先用 update_timeframe 更新",
            }

        df = df.sort_values("trade_datetime")
        records = []
        for _, row in df.iterrows():
            r = {}
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    r[k] = str(v)
                elif isinstance(v, float):
                    r[k] = round(v, 4)
                elif hasattr(v, "item"):
                    r[k] = v.item()
                else:
                    r[k] = v
            records.append(r)

        return {
            "code": code,
            "timeframe": timeframe,
            "label": TIMEFRAMES[timeframe]["label"],
            "count": len(records),
            "kline": records,
        }
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def get_multi_timeframe_data(code: str, timeframes: list[str] = None, limit: int = 50) -> dict:
    """获取多周期数据（用于多周期共振分析）

    Args:
        code: 股票代码
        timeframes: 周期列表，默认 ["daily", "60m", "15m"]
        limit: 每个周期返回条数
    """
    if timeframes is None:
        timeframes = ["daily", "60m", "15m"]

    result = {"code": code, "timeframes": {}}

    for tf in timeframes:
        if tf == "daily":
            from deeppulse.src.query import StockQuery

            query = StockQuery()
            df = query.get_daily_kline(code, limit=limit)
            if not df.empty:
                records = []
                for _, row in df.iterrows():
                    r = {}
                    for k, v in row.items():
                        if hasattr(v, "isoformat"):
                            r[k] = str(v)
                        elif isinstance(v, float):
                            r[k] = round(v, 4)
                        elif hasattr(v, "item"):
                            r[k] = v.item()
                        else:
                            r[k] = v
                    records.append(r)
                result["timeframes"]["daily"] = {
                    "label": "日线",
                    "count": len(records),
                    "kline": records,
                }
        elif tf in TIMEFRAMES:
            data = query_timeframe(code, tf, limit)
            result["timeframes"][tf] = data

    return result
