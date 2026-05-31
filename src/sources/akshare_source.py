"""AkShare 数据源实现"""

import time
from datetime import date

import akshare as ak
import pandas as pd

from .base import DataSourceBase


class AkShareSource(DataSourceBase):
    @property
    def name(self) -> str:
        return "akshare"

    def fetch_stock_list(self) -> list[dict]:
        """通过AkShare获取沪深A股列表，筛选主板"""
        df = ak.stock_info_a_code_name()
        results = []
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            # 筛选主板：沪市6开头，深市0开头（排除创业板3开头、科创板688开头、北交所8开头）
            if code.startswith("6") and not code.startswith("688"):
                market = "sh"
                board = "main"
            elif code.startswith("0"):
                market = "sz"
                board = "main"
            else:
                continue
            results.append(
                {
                    "code": code,
                    "name": row["name"],
                    "market": market,
                    "board": board,
                    "list_date": None,  # AkShare此接口无上市日期
                }
            )
        return results

    def fetch_daily_kline(self, code: str, start_date: date, end_date: date, max_retries: int = 3) -> pd.DataFrame:
        """通过AkShare获取日K线数据（带重试）"""
        symbol = str(code).zfill(6)
        last_err = None
        for attempt in range(max_retries):
            try:
                return self._fetch_kline_impl(symbol, start_date, end_date)
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
        raise last_err

    def _fetch_kline_impl(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",  # 前复权
        )
        if df.empty:
            return pd.DataFrame()

        # 统一列名
        df = df.rename(
            columns={
                "日期": "trade_date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "换手率": "turnover",
            }
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["code"] = symbol
        df["data_source"] = self.name

        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
        return df[[c for c in cols if c in df.columns]]
