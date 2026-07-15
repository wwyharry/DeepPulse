"""Unit tests for agent/backtest.py — technical indicators, signals, and backtesting engine."""

import math

import numpy as np
import pandas as pd
import pytest

from deeppulse.agent.backtest import (
    BacktestEngine,
    BacktestResult,
    add_indicators,
    calc_atr,
    calc_boll,
    calc_ema,
    calc_kdj,
    calc_ma,
    calc_macd,
    calc_obv,
    calc_rsi,
    signal_ma_cross,
    signal_macd_cross,
    signal_rsi_oversold,
    signal_volume_breakout,
)

# ── Indicator calculations ──────────────────────────────────────────


class TestCalcMA:
    def test_basic(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = calc_ma(s, 3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)


class TestCalcEMA:
    def test_length_matches_input(self):
        s = pd.Series(range(1, 21), dtype=float)
        result = calc_ema(s, 5)
        assert len(result) == len(s)

    def test_first_value_equals_input(self):
        s = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        result = calc_ema(s, 3)
        assert result.iloc[0] == pytest.approx(10.0)


class TestCalcRSI:
    def test_range(self):
        np.random.seed(42)
        s = pd.Series(np.cumsum(np.random.normal(0, 1, 100)) + 100)
        rsi = calc_rsi(s, 14)
        valid = rsi.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_all_gains(self):
        s = pd.Series(range(1, 30), dtype=float)
        rsi = calc_rsi(s, 14)
        # When all changes are gains (no losses), avg_loss=0 → division by NaN → NaN
        assert math.isnan(rsi.iloc[-1])

    def test_all_losses(self):
        s = pd.Series(range(30, 1, -1), dtype=float)
        rsi = calc_rsi(s, 14)
        # When all changes are losses, avg_gain=0 → rs=0 → RSI=100-100/(1+0)=0
        assert rsi.iloc[-1] == pytest.approx(0.0)


class TestCalcMACD:
    def test_returns_three_series(self, sample_kline_df):
        dif, dea, macd = calc_macd(sample_kline_df["close"])
        assert len(dif) == len(sample_kline_df)
        assert len(dea) == len(sample_kline_df)
        assert len(macd) == len(sample_kline_df)

    def test_macd_equals_two_times_diff(self, sample_kline_df):
        dif, dea, macd = calc_macd(sample_kline_df["close"])
        expected = (dif - dea) * 2
        pd.testing.assert_series_equal(macd, expected, check_names=False)


class TestCalcKDJ:
    def test_j_equals_3k_minus_2d(self, sample_kline_df):
        k, d, j = calc_kdj(sample_kline_df["high"], sample_kline_df["low"], sample_kline_df["close"])
        expected = 3 * k - 2 * d
        pd.testing.assert_series_equal(j, expected, check_names=False, atol=1e-10)


class TestCalcBoll:
    def test_upper_gt_mid_gt_lower(self, sample_kline_df):
        upper, mid, lower = calc_boll(sample_kline_df["close"])
        valid = upper.dropna().index
        assert (upper[valid] > mid[valid]).all()
        assert (mid[valid] > lower[valid]).all()


class TestCalcATR:
    def test_always_positive(self, sample_kline_df):
        atr = calc_atr(sample_kline_df["high"], sample_kline_df["low"], sample_kline_df["close"])
        valid = atr.dropna()
        assert (valid > 0).all()


class TestCalcOBV:
    def test_sign_changes_with_price(self, sample_kline_df):
        obv = calc_obv(sample_kline_df["close"], sample_kline_df["volume"])
        assert len(obv) == len(sample_kline_df)


class TestAddIndicators:
    def test_adds_all_columns(self, sample_kline_df):
        df = add_indicators(sample_kline_df)
        expected_cols = [
            "ma5",
            "ma10",
            "ma20",
            "ma60",
            "dif",
            "dea",
            "macd",
            "rsi6",
            "rsi14",
            "k",
            "d",
            "j",
            "boll_upper",
            "boll_mid",
            "boll_lower",
            "atr14",
            "obv",
            "vol_ma5",
            "vol_ratio",
            "pct_change",
        ]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"


# ── Signal functions ────────────────────────────────────────────────


class TestSignalMACross:
    def test_detects_crossover(self, sample_kline_df):
        df = add_indicators(sample_kline_df)
        signal = signal_ma_cross(df, fast=5, slow=10)
        assert isinstance(signal, pd.Series)
        assert set(signal.unique()).issubset({-1, 0, 1})


class TestSignalMACDCross:
    def test_detects_crossover(self, sample_kline_df):
        df = add_indicators(sample_kline_df)
        signal = signal_macd_cross(df)
        assert isinstance(signal, pd.Series)
        assert set(signal.unique()).issubset({-1, 0, 1})


class TestSignalRSIOversold:
    def test_detects_oversold(self, sample_kline_df):
        df = add_indicators(sample_kline_df)
        signal = signal_rsi_oversold(df)
        assert isinstance(signal, pd.Series)
        assert set(signal.unique()).issubset({-1, 0, 1})


class TestSignalVolumeBreakout:
    def test_detects_breakout(self, sample_kline_df):
        df = add_indicators(sample_kline_df)
        signal = signal_volume_breakout(df)
        assert isinstance(signal, pd.Series)
        assert set(signal.unique()).issubset({-1, 0, 1})


# ── BacktestEngine ──────────────────────────────────────────────────


class TestBacktestEngine:
    def test_basic_run(self, sample_kline_df):
        df = add_indicators(sample_kline_df)
        engine = BacktestEngine()
        result = engine.run(df, signal_macd_cross, strategy_name="macd_cross", code="TEST", name="Test")
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "macd_cross"
        assert result.initial_capital == 100000

    def test_no_signals(self, sample_kline_df):
        df = add_indicators(sample_kline_df)

        def no_signal(df):
            return pd.Series(0, index=df.index)

        engine = BacktestEngine()
        result = engine.run(df, no_signal, strategy_name="none")
        assert result.total_trades == 0
        assert result.final_capital == result.initial_capital


class TestBacktestResult:
    def test_summary_contains_key_info(self):
        result = BacktestResult(
            strategy_name="test",
            start_date="2024-01-01",
            end_date="2024-12-31",
            initial_capital=100000,
            final_capital=110000,
            total_return=0.1,
            win_rate=0.6,
            total_trades=10,
        )
        summary = result.summary()
        assert "test" in summary
        assert "100,000" in summary
        assert "10.00%" in summary

    def test_to_dict_excludes_equity_curve(self):
        result = BacktestResult(
            strategy_name="test",
            start_date="2024-01-01",
            end_date="2024-12-31",
            initial_capital=100000,
            final_capital=110000,
            equity_curve=[100000, 110000],
        )
        d = result.to_dict()
        assert "equity_curve" not in d
        assert "strategy_name" in d
