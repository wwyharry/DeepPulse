"""实时行情数据类与数据源抽象基类"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RealtimeQuote:
    """统一的实时行情数据结构"""

    code: str
    name: str
    current: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    yesterday_close: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    amount: float | None = None
    trade_date: str = ""
    trade_time: str = ""
    data_source: str = ""

    def to_dict(self) -> dict:
        """转换为字典，None值不包含"""
        d = {}
        for k, v in self.__dict__.items():
            if v is not None and v != "":
                d[k] = round(v, 4) if isinstance(v, float) else v
        return d


class RealtimeQuoteSource(ABC):
    """实时行情数据源抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称标识"""
        ...

    @abstractmethod
    def fetch_quote(self, code: str) -> RealtimeQuote | None:
        """获取单只股票实时行情

        Args:
            code: 6位股票代码

        Returns:
            RealtimeQuote 或 None（停牌/无数据）
        """
        ...

    def fetch_quotes(self, codes: list[str]) -> dict[str, RealtimeQuote]:
        """批量获取实时行情。默认逐个调用 fetch_quote，子类可覆盖优化。

        Args:
            codes: 6位股票代码列表

        Returns:
            {code: RealtimeQuote} 字典，失败的不出现在字典中
        """
        result = {}
        for code in codes:
            try:
                q = self.fetch_quote(code)
                if q is not None:
                    result[code] = q
            except Exception:
                pass
        return result
