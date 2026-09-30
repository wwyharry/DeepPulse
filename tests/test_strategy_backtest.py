"""deeppulse/strategy/backtest.py 固定回归测试。

覆盖已修复缺陷：
(a) 单次完整往返 final_capital 恒等式（含佣成本核算，平仓无双重扣减）
(b) entry_date 落在信号 bar 的下一根（shift(1) 无前视偏差）
(c) 止损退出当日不得立刻买回（exited_this_bar）
(d) 强平 final_cash 与 trades 对账（非未扣卖出成本的市值点）

全部使用合成 DataFrame 直接跑引擎，不依赖网络/真实数据库。
"""

import numpy as np
import pandas as pd
import pytest

from deeppulse.strategy.backtest import BacktestEngine


def _mk_df(closes: list[float]) -> pd.DataFrame:
    """构造合成K线（长度需 ≥30，引擎才会真正撮合）。"""
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(n, 100000.0),
        }
    )


def _signal_at(n: int, points: dict[int, int]) -> pd.Series:
    """构造稀疏信号序列：{bar_index: 1/-1}。"""
    sig = pd.Series(0, index=range(n), dtype=int)
    for i, v in points.items():
        sig.iloc[i] = v
    return sig


class TestRoundTripAccounting:
    def test_final_capital_identity_single_round_trip(self):
        """(a) 平价往返：final == initial - 含佣成本 + 含佣回款，pnl 含双边佣金。"""
        df = _mk_df([10.0] * 40)
        sig = _signal_at(40, {2: 1, 20: -1})
        engine = BacktestEngine(initial_capital=100000, commission=0.001, slippage=0.0, position_pct=1.0)
        result = engine.run(df, lambda d: sig, strategy_name="flat", code="TEST", name="T")

        assert result.total_trades == 1
        trade = result.trades[0]
        shares = trade["shares"]
        # 仓位：100000/(10*1.001) 向下取整到手 → 9900 股
        assert shares == 9900

        cost = shares * trade["entry_price"] * (1 + engine.commission)
        proceeds = shares * trade["exit_price"] * (1 - engine.commission)
        # 恒等式：final_capital == initial - 总成本 + 总回款（无双重扣减）
        assert result.final_capital == pytest.approx(result.initial_capital - cost + proceeds, abs=0.01)
        # 手算：含佣成本 99099，含佣回款 98901 → 99802（双重扣减会得到 99604）
        assert result.final_capital == pytest.approx(99802.0, abs=0.01)
        # pnl 含双边佣金 -198（漏买方佣金会得到 -99）
        assert trade["pnl"] == pytest.approx(-198.0, abs=0.01)


class TestNoLookahead:
    def test_entry_lands_on_bar_after_signal(self):
        """(b) 信号延迟一根 bar 成交：entry/exit 落在信号 bar 的下一根。"""
        df = _mk_df([10.0] * 40)
        sig = _signal_at(40, {5: 1, 15: -1})
        engine = BacktestEngine(initial_capital=100000, commission=0.0, slippage=0.0)
        result = engine.run(df, lambda d: sig, strategy_name="t")

        assert result.total_trades == 1
        trade = result.trades[0]
        assert trade["entry_date"] == str(df["trade_date"].iloc[6])
        assert trade["entry_date"] != str(df["trade_date"].iloc[5])
        assert trade["exit_date"] == str(df["trade_date"].iloc[16])
        assert trade["exit_date"] != str(df["trade_date"].iloc[15])


class TestExitedThisBar:
    def test_stop_loss_day_does_not_rebuy(self):
        """(c) 止损退出当日不得立刻按同一收盘价买回。"""
        # 前 10 根价 10，随后跌到 9.4（-6%，触发 5% 止损）
        df = _mk_df([10.0] * 10 + [9.4] * 30)
        # 第 2 根信号 → 第 3 根成交；第 9 根信号 → 第 10 根（止损当日）生效，禁止买回
        sig = _signal_at(40, {2: 1, 9: 1})
        engine = BacktestEngine(
            initial_capital=100000,
            commission=0.0,
            slippage=0.0,
            position_pct=1.0,
            stop_loss_pct=0.05,
            take_profit_pct=0.10,
            max_hold_days=20,
        )
        result = engine.run(df, lambda d: sig, strategy_name="t")

        assert result.total_trades == 1
        trade = result.trades[0]
        assert trade["exit_reason"] == "止损"
        assert trade["exit_date"] == str(df["trade_date"].iloc[10])
        assert all(t["entry_date"] != str(df["trade_date"].iloc[10]) for t in result.trades)


class TestForcedLiquidationReconciliation:
    def test_final_cash_reconciles_with_trades(self):
        """(d) 回测结束强平后，final_cash 与 trades 对账，而非未扣卖出成本的市值点。"""
        # 前 11 根价 10，随后涨到 11 并持仓到结束
        df = _mk_df([10.0] * 11 + [11.0] * 29)
        sig = _signal_at(40, {1: 1})
        engine = BacktestEngine(
            initial_capital=100000,
            commission=0.001,
            slippage=0.0,
            position_pct=1.0,
            take_profit_pct=0.20,
            max_hold_days=60,
        )
        result = engine.run(df, lambda d: sig, strategy_name="t")

        assert result.total_trades == 1
        trade = result.trades[0]
        assert trade["exit_reason"] == "回测结束平仓"

        cost = trade["shares"] * trade["entry_price"] * (1 + engine.commission)
        proceeds = trade["shares"] * trade["exit_price"] * (1 - engine.commission)
        assert result.final_capital == pytest.approx(result.initial_capital - cost + proceeds, abs=0.01)
        assert result.final_capital == pytest.approx(result.initial_capital + trade["pnl"], abs=0.02)
        # 不得用强平前市值（未扣卖出成本）冒充 final_cash
        assert result.final_capital != pytest.approx(result.equity_curve[-1]["equity"], abs=1.0)


class TestAgentFacade:
    def test_agent_backtest_reuses_strategy_engine(self):
        """agent/backtest.py 是薄门面，必须与 strategy 引擎同一实现（消除孪生引擎）。"""
        import deeppulse.agent.backtest as agent_backtest
        import deeppulse.strategy.backtest as strategy_backtest

        assert agent_backtest.BacktestEngine is strategy_backtest.BacktestEngine
        assert agent_backtest.BacktestResult is strategy_backtest.BacktestResult

    def test_flat_round_trip_through_facade(self):
        """门面入口同样满足手算：final≈99802、pnl≈-198。"""
        from deeppulse.agent.backtest import BacktestEngine as AgentBacktestEngine

        df = _mk_df([10.0] * 40)
        sig = _signal_at(40, {2: 1, 20: -1})
        engine = AgentBacktestEngine(initial_capital=100000, commission=0.001, slippage=0.0, position_pct=1.0)
        result = engine.run(df, lambda d: sig, strategy_name="flat", code="TEST", name="T")

        assert result.final_capital == pytest.approx(99802.0, abs=0.01)
        assert result.trades[0]["pnl"] == pytest.approx(-198.0, abs=0.01)
