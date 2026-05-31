"""东方财富实时行情数据源（基于akshare全市场快照）"""
import time
import threading
from datetime import date, datetime
from typing import Optional

import pandas as pd

from .base import RealtimeQuote, RealtimeQuoteSource


class EastMoneyRealtimeSource(RealtimeQuoteSource):

    def __init__(self, cache_ttl: int = 30):
        """
        Args:
            cache_ttl: 全市场快照缓存有效期（秒），默认30秒
        """
        self._cache_ttl = cache_ttl
        self._snapshot_cache: Optional[pd.DataFrame] = None
        self._snapshot_time: float = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "eastmoney"

    def _get_snapshot(self) -> pd.DataFrame:
        """获取全市场快照，带缓存"""
        import akshare as ak

        now = time.time()
        if self._snapshot_cache is not None and now - self._snapshot_time < self._cache_ttl:
            return self._snapshot_cache

        with self._lock:
            # 双重检查
            if self._snapshot_cache is not None and now - self._snapshot_time < self._cache_ttl:
                return self._snapshot_cache

            df = ak.stock_zh_a_spot_em()
            self._snapshot_cache = df
            self._snapshot_time = time.time()
            return df

    def _find_row(self, code: str) -> Optional[pd.Series]:
        """从快照中按代码查找行"""
        df = self._get_snapshot()
        if df is None or df.empty:
            return None

        code = str(code).zfill(6)
        matches = df[df["代码"].astype(str).str.zfill(6) == code]
        if matches.empty:
            return None
        return matches.iloc[0]

    def fetch_quote(self, code: str) -> Optional[RealtimeQuote]:
        """从东财全市场快照中获取单只股票行情"""
        row = self._find_row(code)
        if row is None:
            return None
        return self._row_to_quote(row)

    def fetch_quotes(self, codes: list[str]) -> dict[str, RealtimeQuote]:
        """东财天然支持批量——一次API调用拿到全市场，直接筛选"""
        df = self._get_snapshot()
        if df is None or df.empty:
            return {}

        codes_set = set(str(c).zfill(6) for c in codes)
        df = df.copy()
        df["code_clean"] = df["代码"].astype(str).str.zfill(6)
        filtered = df[df["code_clean"].isin(codes_set)]

        result = {}
        for _, row in filtered.iterrows():
            quote = self._row_to_quote(row)
            if quote:
                result[quote.code] = quote
        return result

    def _row_to_quote(self, row: pd.Series) -> Optional[RealtimeQuote]:
        """将DataFrame行转换为RealtimeQuote"""
        try:
            current = self._safe_float(row.get("最新价"))
            if current is None or current == 0:
                return None  # 停牌或无数据

            return RealtimeQuote(
                code=str(row.get("代码", "")).zfill(6),
                name=str(row.get("名称", "")),
                current=current,
                open=self._safe_float(row.get("今开")),
                high=self._safe_float(row.get("最高")),
                low=self._safe_float(row.get("最低")),
                yesterday_close=self._safe_float(row.get("昨收")),
                change_amount=round(self._safe_float(row.get("涨跌额")) or 0, 2),
                change_pct=round(self._safe_float(row.get("涨跌幅")) or 0, 2),
                volume=int(self._safe_float(row.get("成交量")) or 0),
                amount=self._safe_float(row.get("成交额")),
                trade_date=str(date.today()),
                trade_time=datetime.now().strftime("%H:%M:%S"),
                data_source="eastmoney",
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        """安全转换为float，处理NaN和空值"""
        if val is None:
            return None
        try:
            f = float(val)
            return None if pd.isna(f) else f
        except (ValueError, TypeError):
            return None

    def invalidate_cache(self):
        """手动失效缓存"""
        self._snapshot_cache = None
        self._snapshot_time = 0
