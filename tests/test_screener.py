"""Unit tests for agent/screener.py — stock screening pure functions."""

import pytest

from deeppulse.agent.screener import _check_conditions, _ema, _extract_number

# ── _ema ────────────────────────────────────────────────────────────


class TestEMA:
    def test_basic(self):
        result = _ema([1, 2, 3, 4, 5], 3)
        assert len(result) == 5
        assert result[0] == 1  # first value equals input

    def test_single_value(self):
        result = _ema([10.0], 3)
        assert result == [10.0]

    def test_monotonic_input(self):
        """EMA of monotonically increasing series should also increase."""
        data = list(range(1, 21))
        result = _ema(data, 5)
        for i in range(1, len(result)):
            assert result[i] >= result[i - 1]


# ── _extract_number ─────────────────────────────────────────────────


class TestExtractNumber:
    def test_from_asterisk(self):
        assert _extract_number("成交量>5日均量*1.5", "*") == pytest.approx(1.5)

    def test_from_gt(self):
        assert _extract_number("涨幅>3%", ">") == pytest.approx(3.0)

    def test_from_lt(self):
        assert _extract_number("RSI6<20", "<") == pytest.approx(20.0)

    def test_no_match(self):
        assert _extract_number("abc", "") == 0

    def test_integer(self):
        assert _extract_number("连板>=2", ">=") == pytest.approx(2.0)


# ── _check_conditions ───────────────────────────────────────────────


class TestCheckConditions:
    def test_insufficient_data(self):
        """Fewer than 20 valid rows → (False, '有效数据不足')"""
        rows = [("2024-01-01", 10.0, 10.5, 9.5, 10.0, 100000, 1e6)] * 5
        match, detail = _check_conditions(rows, "MA5>MA10")
        assert match is False
        assert "不足" in detail

    def test_none_filtered(self):
        """Rows with None values should be filtered out."""
        rows = [("2024-01-01", None, 10.5, 9.5, 10.0, 100000, 1e6)] * 30
        match, detail = _check_conditions(rows, "MA5>MA10")
        assert match is False

    def test_ma5_gt_ma10(self, sample_kline_rows):
        """Test MA condition matching with real-ish data."""
        match, detail = _check_conditions(sample_kline_rows, "MA5>MA10")
        assert isinstance(match, bool)
        assert isinstance(detail, str)

    def test_rsi_condition(self, sample_kline_rows):
        match, detail = _check_conditions(sample_kline_rows, "RSI6<30")
        assert isinstance(match, bool)

    def test_macd_golden_cross(self, sample_kline_rows):
        match, detail = _check_conditions(sample_kline_rows, "MACD金叉")
        assert isinstance(match, bool)
