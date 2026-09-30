"""回测验证框架 - 薄门面，统一委托 deeppulse.strategy.backtest 引擎

历史背景：本模块曾是 deeppulse/strategy/backtest.py 的未修复分叉副本
（信号未 shift(1) 有前视偏差、平仓时 capital 双重扣减、trade.pnl 漏买方佣金）。
现改为薄门面，消除孪生引擎；仅保留旧调用方（charts.py / watchlist.py /
backtest_optimizer.py / tools/analysis.py / tests）依赖的接口名。
"""

from deeppulse.agent.indicators import TechnicalIndicators
from deeppulse.strategy.backtest import (
    SIGNAL_FUNCTIONS,
    BacktestEngine,
    add_indicators,
    signal_boll_bounce,
    signal_kdj_cross,
    signal_ma_cross,
    signal_macd_cross,
    signal_multi_confirm,
    signal_rsi_oversold,
    signal_volume_breakout,
)
from deeppulse.strategy.backtest import (
    backtest_stock as _engine_backtest_stock,
)
from deeppulse.strategy.backtest import (
    backtest_strategy_multi_stock as _engine_backtest_multi_stock,
)
from deeppulse.strategy.models import BacktestResult, Trade

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "Trade",
    "SIGNAL_FUNCTIONS",
    "add_indicators",
    "backtest_stock",
    "backtest_strategy_multi_stock",
    "signal_ma_cross",
    "signal_macd_cross",
    "signal_rsi_oversold",
    "signal_volume_breakout",
    "signal_boll_bounce",
    "signal_kdj_cross",
    "signal_multi_confirm",
]

# ============ 技术指标计算别名（charts.py 依赖） ============

calc_ma = TechnicalIndicators.ma
calc_ema = TechnicalIndicators.ema
calc_rsi = TechnicalIndicators.rsi
calc_macd = TechnicalIndicators.macd
calc_kdj = TechnicalIndicators.kdj
calc_boll = TechnicalIndicators.boll
calc_atr = TechnicalIndicators.atr
calc_obv = TechnicalIndicators.obv


# ============ 便捷函数（保持旧 agent 接口语义） ============


def backtest_stock(
    code: str,
    strategy: str = "macd_cross",
    days: int = 500,
    initial_capital: float = 100000,
    stop_loss: float = 0.05,
    take_profit: float = 0.10,
    max_hold: int = 20,
    **signal_kwargs,
) -> BacktestResult:
    """
    对单只股票运行指定策略的回测

    与旧 agent 接口一致：使用静态止损/止盈/超时（use_dynamic_exit=False），
    stop_loss/take_profit/max_hold 实际生效。底层引擎为 deeppulse.strategy.backtest。

    Args:
        code: 股票代码
        strategy: 策略名（ma_cross / macd_cross / rsi_oversold / volume_breakout /
                  boll_bounce / kdj_cross / multi_confirm）
        days: 回测数据天数（交易日）
        initial_capital: 初始资金
        stop_loss: 止损比例
        take_profit: 止盈比例
        max_hold: 最大持仓天数
        signal_kwargs: 传递给信号函数的额外参数
    """
    return _engine_backtest_stock(
        code,
        strategy=strategy,
        days=days,
        initial_capital=initial_capital,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_hold=max_hold,
        use_dynamic_exit=False,
        **signal_kwargs,
    )


def backtest_strategy_multi_stock(
    strategy: str = "macd_cross", codes: list[str] = None, days: int = 500, top_n: int = 10, **kwargs
) -> dict:
    """
    在多只股票上回测同一策略，汇总统计

    与旧 agent 接口一致：默认静态退出（use_dynamic_exit=False），可用 kwargs 覆盖。
    """
    kwargs.setdefault("use_dynamic_exit", False)
    return _engine_backtest_multi_stock(strategy=strategy, codes=codes, days=days, top_n=top_n, **kwargs)
