"""腾讯行情历史数据源（前复权 qfq）

日K走 ifzq 的 fqkline 接口，与 BaoStock/AkShare 同为前复权口径。
行格式: [日期, 开, 收, 高, 低, 成交量(手), ...]，成交量单位为手，入库前 ×100
换算为股。不提供成交额/换手率（置 NaN，查询层兼容可空列）。

限制：
    - 单次请求最多返回 800 条，超长区间按时间向左分页补齐
    - 无股票列表接口（旧接口已下线），fetch_stock_list 显式报错引导其他源
"""

import logging
from datetime import date, timedelta

import pandas as pd

from deeppulse import config
from deeppulse.src.resilience import RetryPolicy

from .base import DataSourceBase

logger = logging.getLogger(__name__)

_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
# 单次请求服务端最多返回的K线条数（传更大 count 反而回落到默认值）
_PAGE_LIMIT = 800
# 分页防御上限：6 × 800 ≈ 20 年，远超默认 5 年采集窗口
_MAX_PAGES = 6


class TencentSource(DataSourceBase):
    def __init__(self):
        self._session = None
        self._retry_policy = RetryPolicy(
            max_retries=config.RETRY_MAX_RETRIES,
            base_delay=config.RETRY_BASE_DELAY,
            max_delay=config.RETRY_MAX_DELAY,
            backoff_factor=config.RETRY_BACKOFF_FACTOR,
            retryable_errors=(Exception,),  # 异常类型不统一，捕获所有
            fast_fail_conn_errors=False,  # 限频常表现为连接被掐断，退避重试而非快速失败
        )

    @property
    def name(self) -> str:
        return "tencent"

    def _get_session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def cleanup(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    @staticmethod
    def _tencent_symbol(code: str) -> str:
        symbol = str(code).zfill(6)
        prefix = "sh" if symbol.startswith("6") else "sz"
        return f"{prefix}{symbol}"

    def fetch_stock_list(self) -> list[dict]:
        """腾讯无可用的股票列表接口（旧 rank/mktHs 接口已下线）"""
        raise RuntimeError("腾讯源不提供股票列表，请使用 baostock/akshare/eastmoney/sina")

    # ── 日K线 ──────────────────────────────────────────────────────

    def fetch_daily_kline(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """通过腾讯 fqkline 接口获取日K线（前复权 qfq，带重试）"""
        symbol = str(code).zfill(6)
        return self._retry_policy.execute(self._fetch_kline_impl, symbol, start_date, end_date)

    def _fetch_window(self, symbol: str, start_date: date, end_date: date) -> list[list]:
        """拉取单个时间窗的K线（服务端按「最近 N 条」截断，可能缺左端）"""
        param = f"{self._tencent_symbol(symbol)},day,{start_date},{end_date},{_PAGE_LIMIT},qfq"
        resp = self._get_session().get(_KLINE_URL, params={"param": param}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        node = data.get(self._tencent_symbol(symbol)) or {}
        # 有分红的股票返回 qfqday，无复权差异时返回 day
        return node.get("qfqday") or node.get("day") or []

    def _fetch_kline_impl(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        by_date: dict[str, list] = {}
        cur_end = end_date
        for _ in range(_MAX_PAGES):
            batch = self._fetch_window(symbol, start_date, cur_end)
            if not batch:
                break
            for row in batch:
                if len(row) >= 6:
                    by_date[row[0]] = row
            first_day = min(r[0] for r in batch if len(r) >= 6) if any(len(r) >= 6 for r in batch) else ""
            if not first_day or date.fromisoformat(first_day) <= start_date:
                break  # 已覆盖到请求区间左端
            cur_end = date.fromisoformat(first_day) - timedelta(days=1)
            if cur_end < start_date:
                break

        rows = []
        for day in sorted(by_date):
            row = by_date[day]
            rows.append(
                {
                    "trade_date": day,
                    "open": row[1],
                    "close": row[2],
                    "high": row[3],
                    "low": row[4],
                    "volume": row[5],  # 手
                }
            )
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
        if df.empty:
            return pd.DataFrame()
        df["code"] = symbol
        df["data_source"] = self.name
        # 腾讯日K不提供成交额/换手率，置 NaN（列存在以对齐入库 schema）
        df["amount"] = float("nan")
        df["turnover"] = float("nan")

        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # 腾讯成交量单位为手，统一换算为股
        df["volume"] = (pd.to_numeric(df["volume"], errors="coerce") * 100).astype("Int64")

        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
        return df[cols].reset_index(drop=True)
