"""数据源基类定义"""
from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class DataSourceBase(ABC):
    """数据源抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称标识"""
        ...

    @abstractmethod
    def fetch_stock_list(self) -> list[dict]:
        """获取沪深主板股票列表

        Returns:
            list[dict]: 每个dict包含 code, name, market, board, list_date
        """
        ...

    @abstractmethod
    def fetch_daily_kline(self, code: str, start_date: date,
                          end_date: date) -> pd.DataFrame:
        """获取指定股票的日K线数据

        Args:
            code: 股票代码（如 600000）
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame，列: trade_date, open, high, low, close, volume, amount, turnover
        """
        ...

    def fetch_daily_kline_safe(self, code: str, start_date: date,
                               end_date: date) -> pd.DataFrame:
        """带异常处理的日K获取，失败返回空DataFrame"""
        try:
            return self.fetch_daily_kline(code, start_date, end_date)
        except Exception as e:
            print(f"  [{self.name}] {code} 采集失败: {e}")
            return pd.DataFrame()
