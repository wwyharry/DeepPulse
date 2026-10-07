"""东方财富历史数据源（直连公开接口，前复权）

K线走 push2his 的 kline 接口（fqt=1 前复权，与 BaoStock adjustflag=2 /
AkShare qfq 同口径）；股票列表走 push2 的 clist 接口（含上市日期）。

字段口径（klt=101 日线，fields2=f51..f61）：
    日期,开盘,收盘,最高,最低,成交量(手),成交额(元),振幅,涨跌幅,涨跌额,换手率
成交量单位为手，入库前 ×100 换算为股（与 BaoStock/实时行情口径一致）。

接口按主机分片限流：主域被限频/拒连时轮换分片域名重试。
"""

import logging
from datetime import date

import pandas as pd

from deeppulse import config
from deeppulse.src import codes
from deeppulse.src.resilience import RetryPolicy

from .base import DataSourceBase

logger = logging.getLogger(__name__)

# K线与列表接口分属不同主机群；分片域名可绕开单域限频
_KLINE_HOSTS = ["push2his.eastmoney.com", "45.push2his.eastmoney.com", "92.push2his.eastmoney.com"]
_LIST_HOSTS = ["push2.eastmoney.com", "1.push2.eastmoney.com", "82.push2.eastmoney.com"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

# 东财开放接口通用 ut 令牌（前端公开值，非私有凭据）
_UT = "fa5fd1943c7b386f172d6893dbfba10b"


class EastMoneySource(DataSourceBase):
    def __init__(self):
        self._session = None
        self._retry_policy = RetryPolicy(
            max_retries=config.RETRY_MAX_RETRIES,
            base_delay=config.RETRY_BASE_DELAY,
            max_delay=config.RETRY_MAX_DELAY,
            backoff_factor=config.RETRY_BACKOFF_FACTOR,
            retryable_errors=(Exception,),  # 东财异常类型不统一，捕获所有
            fast_fail_conn_errors=False,  # 限频常表现为连接被掐断，退避重试而非快速失败
        )

    @property
    def name(self) -> str:
        return "eastmoney"

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

    # ── HTTP：主机轮换 ──────────────────────────────────────────────

    def _get_json(self, hosts: list[str], path: str, params: dict) -> dict:
        """按主机列表请求 JSON，单主机失败自动换下一个，全部失败抛 ConnectionError"""
        session = self._get_session()
        errors = []
        for host in hosts:
            try:
                resp = session.get(f"https://{host}{path}", params=params, headers=_HEADERS, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:  # noqa: BLE001 — 主机轮换需吞掉单主机异常
                errors.append(f"{host}: {e}")
        raise ConnectionError("东财接口全部主机失败: " + "；".join(errors))

    @staticmethod
    def _secid(code: str) -> str:
        return codes.to_em_secid(code)

    # ── 股票列表 ───────────────────────────────────────────────────

    def fetch_stock_list(self) -> list[dict]:
        """通过东财 clist 接口获取沪深京A股列表（带重试）"""
        return self._retry_policy.execute(self._fetch_stock_list_impl)

    def _fetch_stock_list_impl(self) -> list[dict]:
        results = []
        page = 1
        while page <= 40:  # 防御性翻页上限
            data = self._get_json(
                _LIST_HOSTS,
                "/api/qt/clist/get",
                {
                    "pn": page,
                    "pz": 500,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "ut": _UT,
                    "fs": "m:0+t:6,m:0+t:80,m:0+t:81,m:1+t:2,m:1+t:23",  # 沪深京A股（t:81 北证A股）
                    "fields": "f12,f14,f26",
                },
            )
            diffs = ((data.get("data") or {}).get("diff")) or []
            if not diffs:
                break
            for row in diffs:
                code = str(row.get("f12", "")).zfill(6)
                # 全板块分类（B股/基金/转债等非A股代码在此拒绝）
                info = codes.classify_board(code)
                if info is None:
                    continue
                market, board = info
                raw_date = str(row.get("f26") or "")
                list_date = None
                if len(raw_date) == 8 and raw_date.isdigit():
                    list_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                results.append(
                    {
                        "code": code,
                        "name": row.get("f14"),
                        "market": market,
                        "board": board,
                        "list_date": list_date,
                    }
                )
            if len(diffs) < 500:
                break
            page += 1
        return results

    # ── 日K线 ──────────────────────────────────────────────────────

    def fetch_daily_kline(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """通过东财 kline 接口获取日K线（前复权 fqt=1，带重试）"""
        symbol = str(code).zfill(6)
        return self._retry_policy.execute(self._fetch_kline_impl, symbol, start_date, end_date)

    def _fetch_kline_impl(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        data = self._get_json(
            _KLINE_HOSTS,
            "/api/qt/stock/kline/get",
            {
                "secid": self._secid(symbol),
                "ut": _UT,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",  # 日线
                "fqt": "1",  # 前复权（与 baostock adjustflag=2 / akshare qfq 同口径）
                "beg": start_date.strftime("%Y%m%d"),
                "end": end_date.strftime("%Y%m%d"),
            },
        )
        klines = ((data.get("data") or {}).get("klines")) or []
        if not klines:
            return pd.DataFrame()

        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 11:
                continue
            rows.append(
                {
                    "trade_date": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                    "volume": parts[5],  # 手
                    "amount": parts[6],  # 元
                    "turnover": parts[10],  # 换手率（%）
                }
            )
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["code"] = symbol
        df["data_source"] = self.name

        for col in ["open", "high", "low", "close", "amount", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # 东财成交量单位为手，统一换算为股（与 baostock / 实时行情口径一致）
        df["volume"] = (pd.to_numeric(df["volume"], errors="coerce") * 100).astype("Int64")

        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
        return df[cols]
