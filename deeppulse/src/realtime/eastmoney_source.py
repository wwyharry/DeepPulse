"""东方财富实时行情数据源（基于akshare全市场快照）"""

import logging
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

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

        stamp = self._session_stamp()  # 批量共用一个会话戳
        result = {}
        for _, row in filtered.iterrows():
            quote = self._row_to_quote(row, stamp=stamp)
            if quote:
                result[quote.code] = quote
        return result

    # 集合竞价开始 / 收盘：trade_date 归属的会话日与是否写 trade_time 的边界
    _SESSION_OPEN = dtime(9, 15)
    _SESSION_CLOSE = dtime(15, 0)
    # 收盘后日线回补窗口截止：数据源出完整日线并落库存在延迟，窗口内本地库
    # 尚无今日数据不足以否认今日开市；窗口外仍缺今日数据则按未开市处理
    _BACKFILL_CUTOFF = dtime(17, 0)

    def _session_stamp(self, now: datetime | None = None) -> tuple[str, str]:
        """返回数据所属交易时段日的 (trade_date, trade_time)

        快照是东财全市场缓存里的最后交易数据，非交易时段/节假日查询时
        不能把陈旧快照标成今天。trade_time 仅在实时会话内写当前时间，
        否则置空（to_dict() 会省略空串），不再伪造为 now()。
        """
        now = datetime.now() if now is None else now
        t = now.time()
        if now.weekday() < 5 and t >= self._SESSION_OPEN and self._opened_today(now):
            trade_time = now.strftime("%H:%M:%S") if t <= self._SESSION_CLOSE else ""
            return now.date().isoformat(), trade_time
        return self._recent_trade_date(now), ""

    def _opened_today(self, now: datetime) -> bool:
        """今天是否确认已开市——工作日盘中也不能臆断（法定节假日照常休市）

        东财全市场快照不带最后成交时间字段，确认依据为本地库 MAX(trade_date)
        （可识别节假日）。收盘后日线回补窗口内本地库可能尚无今日数据，不能据此
        否认今日开市；其余情况无法确认今天已开市即返回 False，
        由调用方退回 _recent_trade_date，避免把陈旧快照标成今天。
        """
        db_last = self._db_last_trade_date()
        if db_last is not None and db_last >= now.date():
            return True  # 本地库已有今日数据 → 今日确已开市
        return self._SESSION_CLOSE < now.time() <= self._BACKFILL_CUTOFF

    @staticmethod
    def _db_last_trade_date() -> date | None:
        """本地库 daily_kline 的 MAX(trade_date)；库缺失/无数据/异常返回 None"""
        try:
            from deeppulse import config
            from deeppulse.src.database import get_connection

            if not Path(config.DB_PATH).exists():
                return None
            conn = get_connection()
            try:
                row = conn.execute("SELECT MAX(trade_date) FROM daily_kline").fetchone()
            finally:
                conn.close()
            if row and row[0]:
                return date.fromisoformat(str(row[0]))
        except Exception:
            pass
        return None

    @staticmethod
    def _recent_trade_date(now: datetime) -> str:
        """最近已完结交易日：优先本地库 MAX(trade_date)（可识别节假日），否则回退最近工作日"""
        db_last = EastMoneyRealtimeSource._db_last_trade_date()
        if db_last is not None and (now.date() - db_last).days <= 7:
            # 本地库数据较新时采信（可识别节假日）；陈旧则回退工作日推算
            return db_last.isoformat()
        d = now.date() - timedelta(days=1)  # 盘前/周末：快照属此前交易时段
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.isoformat()

    def _row_to_quote(self, row: pd.Series, stamp: tuple[str, str] | None = None) -> RealtimeQuote | None:
        """将DataFrame行转换为RealtimeQuote（修复数据保真问题）

        Args:
            row: 快照行
            stamp: 可选的 (trade_date, trade_time) 预计算会话戳，批量路径复用
        """
        try:
            current = self._safe_float(row.get("最新价"))
            if current is None or current == 0:
                return None  # 停牌或无数据

            # 修复：change_amount/change_pct 为 None 时保持 None，而非默认 0
            change_amt_raw = self._safe_float(row.get("涨跌额"))
            change_pct_raw = self._safe_float(row.get("涨跌幅"))

            trade_date, trade_time = stamp if stamp else self._session_stamp()

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
                # 东财成交量单位为手，统一换算为股（与 sina / daily_kline 口径一致）
                volume=int(round((self._safe_float(row.get("成交量")) or 0) * 100)),
                amount=self._safe_float(row.get("成交额")),
                trade_date=trade_date,
                trade_time=trade_time,
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
