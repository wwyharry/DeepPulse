"""东方财富实时行情数据源（基于akshare全市场快照）"""

import logging
import threading
import time
from datetime import date, datetime

import pandas as pd

from .base import RealtimeQuote, RealtimeQuoteSource

logger = logging.getLogger(__name__)


class EastMoneyRealtimeSource(RealtimeQuoteSource):
    def __init__(self, cache_ttl: int = 30, timeout: float = 10.0):
        """
        Args:
            cache_ttl: 全市场快照缓存有效期（秒），默认30秒
            timeout: 预留超时参数（akshare 当前不支持）
        """
        self._cache_ttl = cache_ttl
        self._timeout = timeout
        self._snapshot_cache: pd.DataFrame | None = None
        self._snapshot_time: float = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "eastmoney"

    def _get_snapshot(self) -> pd.DataFrame:
        """获取全市场快照，带缓存（修复竞态条件）"""
        import akshare as ak

        # 快速路径（无锁）
        if self._snapshot_cache is not None:
            if time.time() - self._snapshot_time < self._cache_ttl:
                return self._snapshot_cache

        with self._lock:
            # 双重检查 — 重新读取时间，避免竞态导致重复获取
            if self._snapshot_cache is not None:
                if time.time() - self._snapshot_time < self._cache_ttl:
                    return self._snapshot_cache

            df = ak.stock_zh_a_spot_em()
            self._snapshot_cache = df
            self._snapshot_time = time.time()
            return df

    def _find_row(self, code: str) -> pd.Series | None:
        """从快照中按代码查找行"""
        df = self._get_snapshot()
        if df is None or df.empty:
            return None

        code = str(code).zfill(6)
        matches = df[df["代码"].astype(str).str.zfill(6) == code]
        if matches.empty:
            return None
        return matches.iloc[0]

    def fetch_quote(self, code: str) -> RealtimeQuote | None:
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

    def _row_to_quote(self, row: pd.Series) -> RealtimeQuote | None:
        """将DataFrame行转换为RealtimeQuote（修复数据保真问题）"""
        try:
            current = self._safe_float(row.get("最新价"))
            if current is None or current == 0:
                return None  # 停牌或无数据

            # 修复：change_amount/change_pct 为 None 时保持 None，而非默认 0
            change_amt_raw = self._safe_float(row.get("涨跌额"))
            change_pct_raw = self._safe_float(row.get("涨跌幅"))

            return RealtimeQuote(
                code=str(row.get("代码", "")).zfill(6),
                name=str(row.get("名称", "")),
                current=current,
                open=self._safe_float(row.get("今开")),
                high=self._safe_float(row.get("最高")),
                low=self._safe_float(row.get("最低")),
                yesterday_close=self._safe_float(row.get("昨收")),
                change_amount=round(change_amt_raw, 2) if change_amt_raw is not None else None,
                change_pct=round(change_pct_raw, 2) if change_pct_raw is not None else None,
                volume=int(self._safe_float(row.get("成交量")) or 0),
                amount=self._safe_float(row.get("成交额")),
                trade_date=str(date.today()),
                trade_time=datetime.now().strftime("%H:%M:%S"),
                data_source="eastmoney",
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(val) -> float | None:
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
        with self._lock:
            self._snapshot_cache = None
            self._snapshot_time = 0

    def cleanup(self):
        """资源清理"""
        self.invalidate_cache()
