"""新浪财经实时行情数据源"""

import logging
from datetime import date

from .base import RealtimeQuote, RealtimeQuoteSource

logger = logging.getLogger(__name__)


class SinaRealtimeSource(RealtimeQuoteSource):
    def __init__(self, timeout: float = 10.0):
        """
        Args:
            timeout: HTTP 请求超时秒数
        """
        self._timeout = timeout
        self._session = None  # 复用 curl_cffi session

    def _get_session(self):
        """获取或创建复用的 HTTP session"""
        if self._session is None:
            from curl_cffi import requests as curl_requests

            self._session = curl_requests.Session(impersonate="chrome")
        return self._session

    @property
    def name(self) -> str:
        return "sina"

    def _to_sina_code(self, code: str) -> str:
        code = str(code).zfill(6)
        prefix = "sh" if code.startswith("6") else "sz"
        return f"{prefix}{code}"

    def fetch_quote(self, code: str) -> RealtimeQuote | None:
        """通过新浪接口获取单只股票实时行情"""
        code = str(code).zfill(6)
        sina_code = self._to_sina_code(code)
        url = f"https://hq.sinajs.cn/list={sina_code}"
        headers = {"Referer": "https://finance.sina.com.cn"}

        session = self._get_session()
        resp = session.get(url, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace").strip()

        if '=""' in text or not text:
            return None

        return self._parse_response(code, text)

    def fetch_quotes(self, codes: list[str]) -> dict[str, RealtimeQuote]:
        """新浪支持批量查询，一次请求多只股票"""
        if not codes:
            return {}

        sina_codes = [self._to_sina_code(c) for c in codes]
        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        headers = {"Referer": "https://finance.sina.com.cn"}

        session = self._get_session()
        resp = session.get(url, headers=headers, timeout=self._timeout + 5)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace").strip()

        result = {}
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or '=""' in line:
                continue
            try:
                var_part = line.split("=")[0]
                sina_code = var_part.split("_")[-1]
                code = sina_code[2:]  # 去掉 sh/sz 前缀
                quote = self._parse_response(code, line)
                if quote:
                    result[code] = quote
            except (IndexError, ValueError):
                continue
        return result

    def _parse_response(self, code: str, text: str) -> RealtimeQuote | None:
        """解析新浪行情响应文本"""
        try:
            data_str = text.split('"')[1]
            fields = data_str.split(",")

            current = float(fields[3]) if fields[3] else None
            yesterday_close = float(fields[2]) if fields[2] else None

            change_pct = None
            change_amt = None
            if current and yesterday_close and yesterday_close > 0:
                change_amt = round(current - yesterday_close, 2)
                change_pct = round((current - yesterday_close) / yesterday_close * 100, 2)

            return RealtimeQuote(
                code=str(code).zfill(6),
                name=fields[0],
                current=current,
                open=float(fields[1]) if fields[1] else None,
                high=float(fields[4]) if fields[4] else None,
                low=float(fields[5]) if fields[5] else None,
                yesterday_close=yesterday_close,
                change_amount=change_amt,
                change_pct=change_pct,
                volume=int(float(fields[8])) if fields[8] else None,
                amount=float(fields[9]) if fields[9] else None,
                trade_date=fields[30] if len(fields) > 30 else str(date.today()),
                trade_time=fields[31] if len(fields) > 31 else "",
                data_source="sina",
            )
        except (IndexError, ValueError):
            return None

    def cleanup(self):
        """关闭 HTTP session"""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
