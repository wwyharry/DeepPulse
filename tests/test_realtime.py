"""Unit tests for src/realtime/ — RealtimeQuote, Sina parsing, EastMoney parsing, circuit breaker."""

import pandas as pd
import pytest

from deeppulse.src.realtime.base import RealtimeQuote, RealtimeQuoteSource
from deeppulse.src.realtime.eastmoney_source import EastMoneyRealtimeSource
from deeppulse.src.realtime.manager import RealtimeQuoteManager
from deeppulse.src.realtime.sina_source import SinaRealtimeSource
from deeppulse.src.resilience import CircuitState

# ── RealtimeQuote ───────────────────────────────────────────────────


class TestRealtimeQuote:
    def test_to_dict_basic(self):
        q = RealtimeQuote(code="600000", name="浦发银行", current=10.5, open=10.0)
        d = q.to_dict()
        assert d["code"] == "600000"
        assert d["name"] == "浦发银行"
        assert d["current"] == 10.5
        assert d["open"] == 10.0

    def test_to_dict_excludes_none(self):
        q = RealtimeQuote(code="600000", name="浦发银行", current=None, open=10.0)
        d = q.to_dict()
        assert "current" not in d

    def test_to_dict_excludes_empty_string(self):
        q = RealtimeQuote(code="600000", name="浦发银行", trade_date="", trade_time="")
        d = q.to_dict()
        assert "trade_date" not in d
        assert "trade_time" not in d

    def test_to_dict_rounds_floats(self):
        q = RealtimeQuote(code="600000", name="浦发银行", current=10.12345678)
        d = q.to_dict()
        assert d["current"] == pytest.approx(10.1235, abs=0.0001)


# ── SinaRealtimeSource ──────────────────────────────────────────────


class TestSinaSource:
    def test_to_sina_code_sh(self):
        src = SinaRealtimeSource()
        assert src._to_sina_code("600000") == "sh600000"

    def test_to_sina_code_sz(self):
        src = SinaRealtimeSource()
        assert src._to_sina_code("000001") == "sz000001"

    def test_to_sina_code_pads_zeros(self):
        src = SinaRealtimeSource()
        assert src._to_sina_code("1") == "sz000001"

    def test_parse_response_valid(self):
        src = SinaRealtimeSource()
        fields = [
            "浦发银行",  # 0: name
            "10.00",  # 1: open
            "10.10",  # 2: yesterday_close
            "10.50",  # 3: current
            "10.60",  # 4: high
            "9.90",  # 5: low
            "10.50",  # 6
            "10.51",  # 7
            "1234567",  # 8: volume
            "12345678.00",  # 9: amount
        ]
        fields.extend([""] * 22)
        fields.append("2024-01-15")  # 30: date
        fields.append("14:30:00")  # 31: time
        text = f'var hq_str_sh600000="{",".join(fields)}";'

        quote = src._parse_response("600000", text)
        assert quote is not None
        assert quote.code == "600000"
        assert quote.name == "浦发银行"
        assert quote.current == pytest.approx(10.5)
        assert quote.open == pytest.approx(10.0)
        assert quote.high == pytest.approx(10.6)
        assert quote.low == pytest.approx(9.9)
        assert quote.yesterday_close == pytest.approx(10.1)
        assert quote.change_amount == pytest.approx(0.4)
        assert quote.data_source == "sina"

    def test_parse_response_empty(self):
        src = SinaRealtimeSource()
        quote = src._parse_response("600000", 'var hq_str_sh600000="";')
        assert quote is None

    def test_custom_timeout(self):
        """SinaRealtimeSource accepts custom timeout."""
        src = SinaRealtimeSource(timeout=5.0)
        assert src._timeout == 5.0


# ── EastMoneyRealtimeSource ─────────────────────────────────────────


class TestEastMoneySource:
    def test_safe_float_normal(self):
        assert EastMoneyRealtimeSource._safe_float(3.14) == pytest.approx(3.14)

    def test_safe_float_none(self):
        assert EastMoneyRealtimeSource._safe_float(None) is None

    def test_safe_float_nan(self):
        assert EastMoneyRealtimeSource._safe_float(float("nan")) is None

    def test_safe_float_string(self):
        assert EastMoneyRealtimeSource._safe_float("not_a_number") is None

    def test_safe_float_zero(self):
        assert EastMoneyRealtimeSource._safe_float(0) == pytest.approx(0.0)

    def test_row_to_quote_valid(self):
        src = EastMoneyRealtimeSource()
        row = pd.Series(
            {
                "代码": "600000",
                "名称": "浦发银行",
                "最新价": 10.5,
                "今开": 10.0,
                "最高": 10.6,
                "最低": 9.9,
                "昨收": 10.1,
                "涨跌额": 0.4,
                "涨跌幅": 3.96,
                "成交量": 1234567,
                "成交额": 12345678.0,
            }
        )
        quote = src._row_to_quote(row)
        assert quote is not None
        assert quote.code == "600000"
        assert quote.name == "浦发银行"
        assert quote.current == pytest.approx(10.5)

    def test_row_to_quote_zero_price(self):
        """Suspended stock with price=0 should return None."""
        src = EastMoneyRealtimeSource()
        row = pd.Series(
            {
                "代码": "600000",
                "名称": "浦发银行",
                "最新价": 0,
                "今开": 0,
                "最高": 0,
                "最低": 0,
                "昨收": 10.0,
            }
        )
        quote = src._row_to_quote(row)
        assert quote is None

    def test_row_to_quote_none_change_preserved(self):
        """None change_amount/change_pct should remain None, not default to 0."""
        src = EastMoneyRealtimeSource()
        row = pd.Series(
            {
                "代码": "600000",
                "名称": "浦发银行",
                "最新价": 10.5,
                "今开": 10.0,
                "最高": 10.6,
                "最低": 9.9,
                "昨收": 10.1,
                "涨跌额": None,  # 无数据
                "涨跌幅": None,  # 无数据
                "成交量": 1234567,
                "成交额": 12345678.0,
            }
        )
        quote = src._row_to_quote(row)
        assert quote is not None
        assert quote.change_amount is None  # 不应默认为 0
        assert quote.change_pct is None


# ── RealtimeQuoteManager (circuit breaker) ──────────────────────────


class _MockSource(RealtimeQuoteSource):
    """A mock source that can be configured to succeed or fail."""

    def __init__(self, name, behavior="success", quote=None, timeout=10.0):
        self._name = name
        self._behavior = behavior
        self._quote = quote or RealtimeQuote(code="600000", name="test", current=10.0)
        self.call_count = 0

    @property
    def name(self):
        return self._name

    def fetch_quote(self, code):
        self.call_count += 1
        if self._behavior == "fail":
            raise ConnectionError(f"{self._name} failed")
        return self._quote


class TestRealtimeQuoteManager:
    def test_priority_order(self):
        """First source succeeds, second should not be called."""
        src1 = _MockSource("primary", behavior="success")
        src2 = _MockSource("backup", behavior="success")

        mgr = RealtimeQuoteManager(priority=["primary", "backup"])
        mgr._sources = {"primary": src1, "backup": src2}

        quote = mgr.fetch_quote("600000")
        assert quote is not None
        assert src1.call_count == 1
        assert src2.call_count == 0

    def test_fallback_on_failure(self):
        """First source fails, second should be tried."""
        src1 = _MockSource("primary", behavior="fail")
        src2 = _MockSource("backup", behavior="success")

        mgr = RealtimeQuoteManager(priority=["primary", "backup"])
        mgr._sources = {"primary": src1, "backup": src2}

        quote = mgr.fetch_quote("600000")
        assert quote is not None
        assert src1.call_count == 1
        assert src2.call_count == 1

    def test_circuit_breaker_opens_after_threshold(self):
        """After threshold failures, source should be circuit-broken."""
        src1 = _MockSource("primary", behavior="fail")
        src2 = _MockSource("backup", behavior="success")

        mgr = RealtimeQuoteManager(priority=["primary", "backup"])
        mgr._sources = {"primary": src1, "backup": src2}

        # Trigger failures up to threshold
        for _ in range(3):
            mgr.fetch_quote("600000")

        # Now primary's circuit breaker should be open
        breaker = mgr._get_breaker("primary")
        assert breaker.state == CircuitState.OPEN

        # Next call should skip primary entirely
        src1.call_count = 0
        src2.call_count = 0
        mgr.fetch_quote("600000")
        assert src1.call_count == 0  # skipped due to circuit breaker
        assert src2.call_count == 1

    def test_circuit_breaker_recovers(self):
        """After cooldown period, source should become available again via half-open."""
        src = _MockSource("primary", behavior="success")

        mgr = RealtimeQuoteManager(priority=["primary"])
        mgr._sources = {"primary": src}

        # Manually set breaker to open with expired cooldown
        breaker = mgr._get_breaker("primary")
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Simulate recovery timeout by resetting
        breaker.reset()
        assert breaker.state == CircuitState.CLOSED

        quote = mgr.fetch_quote("600000")
        assert quote is not None

    def test_all_sources_down(self):
        """When all sources fail, return None."""
        src1 = _MockSource("primary", behavior="fail")
        src2 = _MockSource("backup", behavior="fail")

        mgr = RealtimeQuoteManager(priority=["primary", "backup"])
        mgr._sources = {"primary": src1, "backup": src2}

        quote = mgr.fetch_quote("600000")
        assert quote is None

    def test_batch_fallback(self):
        """Batch fetch should fall back to second source for remaining codes."""
        src1 = _MockSource("primary", behavior="success")
        src2 = _MockSource("backup", behavior="success")

        # Primary only returns some codes
        def primary_fetch_quotes(codes):
            return {codes[0]: RealtimeQuote(code=codes[0], name="test", current=10.0)}

        src1.fetch_quotes = primary_fetch_quotes

        mgr = RealtimeQuoteManager(priority=["primary", "backup"])
        mgr._sources = {"primary": src1, "backup": src2}

        result = mgr.fetch_quotes(["600000", "000001"])
        assert len(result) == 2
