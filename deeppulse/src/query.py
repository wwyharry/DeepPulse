"""Agent友好的查询接口 - 封装常用查询操作"""

import pandas as pd

from deeppulse import config
from deeppulse.src.database import get_connection


class StockQuery:
    """A股日K数据库查询接口，供Agent使用"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    def _conn(self):
        return get_connection(self.db_path)

    def get_stock_info(self, code: str = None) -> pd.DataFrame:
        """获取股票信息。不传code返回全部。"""
        conn = self._conn()
        if code:
            df = conn.execute("SELECT * FROM stock_info WHERE code = ?", [code]).fetchdf()
        else:
            df = conn.execute("SELECT * FROM stock_info ORDER BY code").fetchdf()
        conn.close()
        return df

    def get_daily_kline(
        self, code: str, start_date: str = None, end_date: str = None, source: str = None, limit: int = None
    ) -> pd.DataFrame:
        """获取单只股票日K线数据

        Args:
            code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            source: 指定数据源
            limit: 返回行数限制
        """
        conn = self._conn()
        sql = "SELECT * FROM daily_kline WHERE code = ?"
        params = [code]

        if start_date:
            sql += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND trade_date <= ?"
            params.append(end_date)
        if source:
            sql += " AND data_source = ?"
            params.append(source)

        sql += " ORDER BY trade_date"
        if limit:
            sql = f"""
                SELECT * FROM (
                    {sql.replace("ORDER BY trade_date", "ORDER BY trade_date DESC")}
                    LIMIT ?
                ) ORDER BY trade_date
            """
            params.append(int(limit))

        df = conn.execute(sql, params).fetchdf()
        conn.close()
        return df

    def get_multi_stock_kline(self, codes: list[str], start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取多只股票日K数据，用于横向对比"""
        conn = self._conn()
        placeholders = ",".join(["?" for _ in codes])
        sql = f"SELECT * FROM daily_kline WHERE code IN ({placeholders})"
        params = list(codes)

        if start_date:
            sql += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND trade_date <= ?"
            params.append(end_date)

        sql += " ORDER BY code, trade_date"
        df = conn.execute(sql, params).fetchdf()
        conn.close()
        return df

    def get_latest_price(self, codes: list[str] = None) -> pd.DataFrame:
        """获取最新价格。不传codes返回全部股票最新价。"""
        conn = self._conn()
        sql = """
            SELECT k.code, s.name, k.trade_date, k.open, k.high, k.low,
                   k.close, k.volume, k.amount, k.turnover, k.data_source
            FROM daily_kline k
            JOIN stock_info s ON k.code = s.code
            WHERE k.trade_date = (
                SELECT MAX(trade_date) FROM daily_kline WHERE code = k.code
            )
        """
        params = []
        if codes:
            placeholders = ",".join(["?" for _ in codes])
            sql += f" AND k.code IN ({placeholders})"
            params = list(codes)

        sql += " ORDER BY k.code"
        df = conn.execute(sql, params).fetchdf()
        conn.close()
        return df

    def search_stocks(self, keyword: str) -> pd.DataFrame:
        """按名称或代码搜索股票"""
        conn = self._conn()
        df = conn.execute(
            "SELECT * FROM stock_info WHERE code LIKE ? OR name LIKE ? ORDER BY code", [f"%{keyword}%", f"%{keyword}%"]
        ).fetchdf()
        conn.close()
        return df

    def get_data_stats(self) -> dict:
        """获取数据库统计信息"""
        conn = self._conn()
        stats = {}
        stats["stock_count"] = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
        stats["kline_count"] = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
        stats["date_range"] = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_kline").fetchone()
        stats["sources"] = (
            conn.execute("SELECT data_source, COUNT(*) FROM daily_kline GROUP BY data_source")
            .fetchdf()
            .to_dict("records")
        )
        conn.close()
        return stats

    def get_fetch_log(self, code: str = None, status: str = None, limit: int = 100) -> pd.DataFrame:
        """查询采集日志"""
        conn = self._conn()
        sql = "SELECT * FROM fetch_log WHERE 1=1"
        params = []
        if code:
            sql += " AND code = ?"
            params.append(code)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += f" ORDER BY fetched_at DESC LIMIT {limit}"
        df = conn.execute(sql, params).fetchdf()
        conn.close()
        return df
