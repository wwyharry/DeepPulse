"""新浪财经历史数据源（不复权，多源兜底的最后一环）

日K走 getKLineData 接口（scale=240 日线）。该接口为**不复权价**（与腾讯
unadjusted 一致），与 BaoStock/AkShare/东财/腾讯的前复权口径不同：仅在
除权除息日附近拼接时会出现价格跳变。作为兜底链的最后一环：前几个源都
不可用时宁可给不复权数据，也不让分析无数据可用。

限制：
    - 返回「最近 N 条」而非任意日期窗口（datalen 上限足够覆盖 10 年+）
    - 不提供成交额/换手率（置 NaN）
    - 股票列表无上市日期
"""

import logging
from datetime import date

import pandas as pd

from deeppulse import config
from deeppulse.src.resilience import RetryPolicy

from .base import DataSourceBase

logger = logging.getLogger(__name__)

_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
_LIST_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
_HEADERS = {"Referer": "https://finance.sina.com.cn"}
# 最近 N 条日K；2000 条 ≈ 8 年，覆盖默认 5 年采集窗口
_KLINE_DATALEN = 2000


class SinaSource(DataSourceBase):
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
        return "sina"

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
    def _sina_symbol(code: str) -> str:
        symbol = str(code).zfill(6)
        prefix = "sh" if symbol.startswith("6") else "sz"
        return f"{prefix}{symbol}"

    # ── 股票列表 ───────────────────────────────────────────────────

    def fetch_stock_list(self) -> list[dict]:
        """通过新浪 Market_Center 分页获取沪深主板股票列表（带重试）"""
        return self._retry_policy.execute(self._fetch_stock_list_impl)

    def _fetch_stock_list_impl(self) -> list[dict]:
        results = []
        page = 1
        while page <= 80:  # 防御性翻页上限（hs_a 含非主板，翻页数偏多）
            resp = self._get_session().get(
                _LIST_URL,
                params={"page": page, "num": 100, "sort": "symbol", "asc": 1, "node": "hs_a", "_s_r_a": "page"},
                headers=_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json() or []
            if not batch:
                break
            for row in batch:
                symbol = str(row.get("symbol") or "")
                code = str(row.get("code") or "").zfill(6)
                # hs_a 节点混有科创板/创业板/北交所，仅保留沪深主板
                if symbol.startswith("sh6") and not symbol.startswith("sh68"):
                    market, board = "sh", "main"
                elif symbol.startswith("sz0"):
                    market, board = "sz", "main"
                else:
                    continue
                results.append(
                    {
                        "code": code,
                        "name": row.get("name"),
                        "market": market,
                        "board": board,
                        "list_date": None,  # 该接口无上市日期
                    }
                )
            if len(batch) < 100:
                break
            page += 1
        return results

    # ── 日K线 ──────────────────────────────────────────────────────

    def fetch_daily_kline(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """通过新浪 getKLineData 获取日K线（**不复权**，带重试）

        接口只回最近 N 条，按请求窗口过滤；请求区间超出 N 条覆盖范围时
        仅返回可覆盖的部分（调用方断点续传会再补）。
        """
        symbol = str(code).zfill(6)
        return self._retry_policy.execute(self._fetch_kline_impl, symbol, start_date, end_date)

    def _fetch_kline_impl(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        resp = self._get_session().get(
            _KLINE_URL,
            params={"symbol": self._sina_symbol(symbol), "scale": 240, "ma": "no", "datalen": _KLINE_DATALEN},
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        klines = resp.json() or []
        if not klines:
            return pd.DataFrame()

        rows = []
        for row in klines:
            day = row.get("day")
            if not day:
                continue
            d = date.fromisoformat(day[:10])
            if start_date <= d <= end_date:
                rows.append(
                    {
                        "trade_date": d,
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "volume": row.get("volume"),  # 股（与库表口径一致，不换算）
                    }
                )
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["code"] = symbol
        df["data_source"] = self.name
        # 新浪日K不提供成交额/换手率，置 NaN（列存在以对齐入库 schema）
        df["amount"] = float("nan")
        df["turnover"] = float("nan")

        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
        return df[cols].reset_index(drop=True)
