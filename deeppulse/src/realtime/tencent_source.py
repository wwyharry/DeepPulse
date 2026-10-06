"""腾讯行情实时数据源（qt.gtimg.cn）

批量接口返回 v_shXXXXXX="..." 文本（GBK），字段以 ~ 分隔：
    1 名称 / 2 代码 / 3 现价 / 4 昨收 / 5 今开 / 6 成交量(手)
    29 最近逐笔成交（常为空） / 30 最后成交时间戳 YYYYMMDDHHMMSS（真实成交时间）
    31 涨跌额 / 32 涨跌幅 / 33 最高 / 34 最低
    35 现价/成交量(手)/成交额(元) / 36 成交量(手) / 37 成交额(万)

带真实成交时间戳，trade_date/trade_time 可直接采信，无需 stale_hint 保守标注。
成交量单位为手，构造前 ×100 归一为股。
"""

import logging

from .base import RealtimeQuote, RealtimeQuoteSource

logger = logging.getLogger(__name__)

_QUOTE_URL = "https://qt.gtimg.cn/q="
# 单次批量上限（URL 长度与服务端限制的保守值）
_BATCH_SIZE = 50


class TencentRealtimeSource(RealtimeQuoteSource):
    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout
        self._session = None  # 复用 curl_cffi session

    def _get_session(self):
        if self._session is None:
            from curl_cffi import requests as curl_requests

            self._session = curl_requests.Session(impersonate="chrome")
        return self._session

    @property
    def name(self) -> str:
        return "tencent"

    def _to_tencent_code(self, code: str) -> str:
        code = str(code).zfill(6)
        prefix = "sh" if code.startswith("6") else "sz"
        return f"{prefix}{code}"

    def fetch_quote(self, code: str) -> RealtimeQuote | None:
        """通过腾讯接口获取单只股票实时行情"""
        return self.fetch_quotes([code]).get(str(code).zfill(6))

    def fetch_quotes(self, codes: list[str]) -> dict[str, RealtimeQuote]:
        """腾讯支持批量查询，一次请求多只股票（超上限自动分批）"""
        if not codes:
            return {}

        result: dict[str, RealtimeQuote] = {}
        clean_codes = [str(c).zfill(6) for c in codes]
        for i in range(0, len(clean_codes), _BATCH_SIZE):
            chunk = clean_codes[i : i + _BATCH_SIZE]
            tencent_codes = [self._to_tencent_code(c) for c in chunk]
            resp = self._get_session().get(_QUOTE_URL + ",".join(tencent_codes), timeout=self._timeout + 5)
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace").strip()
            for line in text.splitlines():
                line = line.strip()
                if not line or '="' not in line:
                    continue
                try:
                    var_part = line.split("=")[0]
                    tencent_code = var_part.split("_")[-1]
                    code = tencent_code[2:]  # 去掉 sh/sz 前缀
                    quote = self._parse_response(code, line)
                    if quote:
                        result[str(code).zfill(6)] = quote
                except (IndexError, ValueError) as e:
                    logger.debug(f"[tencent] 行解析失败: {line[:80]} ({e})")
        return result

    def _parse_response(self, code: str, text: str) -> RealtimeQuote | None:
        """解析腾讯行情响应行

        Returns:
            RealtimeQuote；空响应/停牌零价返回 None

        Raises:
            ValueError: 负载损坏/字段错乱无法解析（计入熔断失败，便于切备源）
        """
        try:
            data_str = text.split('"')[1]
        except IndexError as e:
            raise ValueError(f"腾讯响应不含引号字段，无法解析: {text[:80]}") from e
        if not data_str or data_str == "1":
            return None  # 空响应：无效代码/无数据

        try:
            fields = data_str.split("~")
            if len(fields) < 35:
                raise ValueError(f"字段数不足: {len(fields)}")

            current = float(fields[3]) if fields[3] else None
            # 停牌/无数据：零价过滤，与 sina/eastmoney 语义一致
            if current is None or current <= 0:
                return None

            yesterday_close = float(fields[4]) if fields[4] else None
            change_amt = float(fields[31]) if fields[31] else None
            change_pct = float(fields[32]) if fields[32] else None

            # 成交额优先取字段35的精确元值，缺失时退回字段37（万元）
            amount = None
            parts35 = fields[35].split("/") if len(fields) > 35 else []
            if len(parts35) >= 3 and parts35[2]:
                amount = float(parts35[2])
            elif len(fields) > 37 and fields[37]:
                amount = float(fields[37]) * 10000

            volume = None
            raw_vol = fields[6] or (fields[36] if len(fields) > 36 else "")
            if raw_vol:
                volume = int(float(raw_vol) * 100)  # 手 → 股

            trade_date, trade_time = "", ""
            stamp = fields[30] if len(fields) > 30 else ""
            if len(stamp) == 14 and stamp.isdigit():
                trade_date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
                trade_time = f"{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}"

            return RealtimeQuote(
                code=str(code).zfill(6),
                name=fields[1],
                current=current,
                open=float(fields[5]) if fields[5] else None,
                high=float(fields[33]) if fields[33] else None,
                low=float(fields[34]) if fields[34] else None,
                yesterday_close=yesterday_close,
                change_amount=change_amt,
                change_pct=change_pct,
                volume=volume,
                amount=amount,
                trade_date=trade_date,
                trade_time=trade_time,
                data_source="tencent",
                # 时间戳为真实成交时间，无需 stale_hint 保守标注
            )
        except (IndexError, ValueError) as e:
            raise ValueError(f"腾讯行情字段解析失败: {data_str[:80]}") from e

    def cleanup(self):
        """关闭 HTTP session"""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
