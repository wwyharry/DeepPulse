"""Unit tests for agent/patterns.py — K-line pattern recognition (all pure functions)."""

import numpy as np
import pandas as pd
import pytest

from agent.patterns import (
    _body,
    _body_ratio,
    _double_candle_patterns,
    _is_bearish,
    _is_bullish,
    _lower_shadow,
    _platform_patterns,
    _range,
    _single_candle_patterns,
    _triple_candle_patterns,
    _upper_shadow,
    format_patterns,
    recognize_patterns,
)

# ── Helper to build a single-row DataFrame ──────────────────────────


def _make_df(open_, high, low, close, volume=100000.0):
    return pd.DataFrame(
        {
            "open": [open_],
            "high": [high],
            "low": [low],
            "close": [close],
            "volume": [volume],
        }
    )


def _make_multi_df(rows):
    """rows: list of (open, high, low, close, volume)"""
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [r[4] for r in rows],
        }
    )


# ── Helper functions ────────────────────────────────────────────────


class TestHelperFunctions:
    def test_body_bullish(self):
        row = _make_df(10.0, 11.0, 9.0, 10.5).iloc[0]
        assert _body(row) == pytest.approx(0.5)

    def test_body_bearish(self):
        row = _make_df(10.5, 11.0, 9.0, 10.0).iloc[0]
        assert _body(row) == pytest.approx(0.5)

    def test_upper_shadow(self):
        row = _make_df(10.0, 12.0, 9.0, 10.5).iloc[0]
        assert _upper_shadow(row) == pytest.approx(1.5)

    def test_lower_shadow(self):
        row = _make_df(10.0, 11.0, 8.0, 10.5).iloc[0]
        assert _lower_shadow(row) == pytest.approx(2.0)

    def test_range(self):
        row = _make_df(10.0, 12.0, 8.0, 10.5).iloc[0]
        assert _range(row) == pytest.approx(4.0)

    def test_is_bullish(self):
        row = _make_df(10.0, 11.0, 9.0, 10.5).iloc[0]
        assert _is_bullish(row) is True

    def test_is_bearish(self):
        row = _make_df(10.5, 11.0, 9.0, 10.0).iloc[0]
        assert _is_bearish(row) is True

    def test_body_ratio_doji(self):
        # open == close, so body is 0
        row = _make_df(10.0, 11.0, 9.0, 10.0).iloc[0]
        assert _body_ratio(row) == pytest.approx(0.0)


# ── Pattern recognition ─────────────────────────────────────────────


class TestSingleCandlePatterns:
    def test_doji(self):
        """Open == close with balanced shadows → 十字星"""
        df = _make_df(10.0, 10.5, 9.5, 10.0)
        patterns = _single_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "十字星" in names

    def test_hammer(self):
        """Long lower shadow, small body near top → 锤子线 (requires downtrend context)"""
        # Condition: lower > body*2 AND upper < body*0.5 AND body > 0
        # Last row: open=9.0, high=9.3, low=7.0, close=9.2 → body=0.2, lower=2.2, upper=0.1
        # lower(2.2) > body*2(0.4) ✓, upper(0.1) < body*0.5(0.1) → 0.1 < 0.1 is False!
        # Need upper strictly less: make body=0.3 → upper < 0.15
        # open=9.0, high=9.14, low=7.0, close=9.3 → body=0.3, lower=2.3, upper=0.14
        # Wait, close > high is invalid. Let's use: open=9.0, high=9.14, low=7.0, close=9.3 is invalid.
        # Correct: open=9.0, high=9.14, low=7.0, close=9.3 → NO, close must be <= high.
        # Use: open=9.0, high=9.14, low=7.0, close=9.3 is impossible (close>high).
        # Use: open=9.0, high=9.3, low=7.0, close=9.2 → body=0.2, lower=2.2, upper=0.1
        # upper(0.1) < body*0.5(0.1) → False (not strictly less). Need smaller upper.
        # Use: open=9.0, high=9.12, low=7.0, close=9.3 → invalid (close>high)
        # Use: open=9.0, close=9.3 → body=0.3. high must be >= close=9.3. Use high=9.35 → upper=0.05.
        # low=7.0 → lower=2.3. lower(2.3) > body*2(0.6) ✓. upper(0.05) < body*0.5(0.15) ✓.
        df = _make_multi_df([
            (11.0, 11.1, 10.5, 10.6, 100000),
            (10.6, 10.7, 10.0, 10.1, 100000),
            (10.1, 10.2, 9.5, 9.6, 100000),
            (9.6, 9.7, 9.0, 9.1, 100000),
            (9.0, 9.35, 7.0, 9.3, 100000),  # hammer: body=0.3, lower=2.3, upper=0.05
        ])
        patterns = _single_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "锤子线" in names

    def test_shooting_star(self):
        """Long upper shadow, small body near bottom → 射击之星 (requires uptrend context)"""
        # Condition: upper > body*2 AND lower < body*0.5 AND body > 0
        # Last row: open=10.0, high=12.0, low=10.02, close=10.1 → body=0.1, upper=1.9, lower=0.02
        # upper(1.9) > body*2(0.2) ✓, lower(0.02) < body*0.5(0.05) ✓
        df = _make_multi_df([
            (9.0, 9.1, 8.5, 9.0, 100000),
            (9.0, 9.5, 8.9, 9.4, 100000),
            (9.4, 9.9, 9.3, 9.8, 100000),
            (9.8, 10.3, 9.7, 10.2, 100000),
            (10.0, 12.0, 10.02, 10.1, 100000),  # shooting star: body=0.1, upper=1.9, lower=0.02
        ])
        patterns = _single_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "射击之星" in names

    def test_big_bullish_candle(self):
        """Large bullish candle → 大阳线"""
        df = _make_df(10.0, 11.0, 9.9, 10.8)
        patterns = _single_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "大阳线" in names

    def test_big_bearish_candle(self):
        """Large bearish candle → 大阴线"""
        df = _make_df(10.8, 10.9, 9.9, 10.0)
        patterns = _single_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "大阴线" in names


class TestDoubleCandlePatterns:
    def test_bullish_engulfing(self):
        """Bearish candle followed by larger bullish candle → 看涨吞没"""
        df = _make_multi_df(
            [
                (10.5, 10.6, 10.0, 10.1, 100000),  # bearish
                (10.0, 10.8, 9.9, 10.7, 150000),  # bullish, engulfs prev
            ]
        )
        patterns = _double_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "看涨吞没" in names

    def test_bearish_engulfing(self):
        """Bullish candle followed by larger bearish candle → 看跌吞没"""
        df = _make_multi_df(
            [
                (10.0, 10.5, 9.9, 10.4, 100000),  # bullish
                (10.5, 10.6, 9.8, 9.9, 150000),  # bearish, engulfs prev
            ]
        )
        patterns = _double_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "看跌吞没" in names


class TestTripleCandlePatterns:
    def test_morning_star(self):
        """Bearish → small body → bullish = 早晨之星"""
        df = _make_multi_df(
            [
                (10.5, 10.6, 9.8, 9.9, 100000),  # bearish
                (9.9, 10.0, 9.8, 9.95, 50000),  # small body
                (10.0, 10.8, 10.0, 10.7, 120000),  # bullish
            ]
        )
        patterns = _triple_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "早晨之星" in names

    def test_three_white_soldiers(self):
        """Three consecutive bullish candles with strictly increasing bodies → 红三兵"""
        df = _make_multi_df(
            [
                (10.0, 10.3, 9.9, 10.2, 100000),  # body = 0.2
                (10.2, 10.7, 10.1, 10.6, 110000),  # body = 0.4
                (10.6, 11.3, 10.5, 11.2, 120000),  # body = 0.6
            ]
        )
        patterns = _triple_candle_patterns(df)
        names = [p["name"] for p in patterns]
        assert "红三兵" in names


class TestPlatformPatterns:
    def test_consolidation(self):
        """15+ candles with < 5% price variation → 横盘整理"""
        np.random.seed(99)
        n = 20
        base = 10.0
        rows = []
        for _i in range(n):
            p = base * (1 + np.random.uniform(-0.01, 0.01))
            rows.append((p, p * 1.005, p * 0.995, p, 100000.0))
        df = _make_multi_df(rows)
        patterns = _platform_patterns(df)
        names = [p["name"] for p in patterns]
        assert "横盘整理" in names


class TestRecognizePatterns:
    def test_empty_df(self):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert recognize_patterns(df) == []

    def test_short_df(self):
        df = _make_df(10.0, 11.0, 9.0, 10.0)
        # Single row should still return some single-candle patterns
        patterns = recognize_patterns(df)
        assert isinstance(patterns, list)

    def test_full_pipeline(self):
        """recognize_patterns aggregates all sub-detectors."""
        df = _make_multi_df(
            [
                (10.0, 10.5, 9.5, 10.0, 100000),  # doji
                (10.0, 10.8, 9.9, 10.7, 150000),  # bullish
            ]
        )
        patterns = recognize_patterns(df)
        assert isinstance(patterns, list)


class TestFormatPatterns:
    def test_empty(self):
        result = format_patterns([])
        assert "未识别到" in result

    def test_with_data(self):
        patterns = [
            {"name": "十字星", "type": "neutral", "confidence": 0.8, "description": "test"},
        ]
        result = format_patterns(patterns)
        assert "十字星" in result
        # Confidence rendered as filled/empty circles: int(0.8*5)=4 filled, 1 empty
        assert "●●●●○" in result
        assert "test" in result
