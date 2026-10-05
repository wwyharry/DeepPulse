"""回测验证框架 - 薄门面，统一委托 deeppulse.strategy.backtest 引擎

历史背景：本模块曾是 deeppulse/strategy/backtest.py 的未修复分叉副本
（信号未 shift(1) 有前视偏差、平仓时 capital 双重扣减、trade.pnl 漏买方佣金）。
现改为薄门面，消除孪生引擎；仅保留旧调用方（charts.py / watchlist.py / tests）
依赖的接口名。
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
from deeppulse.strategy.models import BacktestResult, Trade

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "Trade",
    "SIGNAL_FUNCTIONS",
    "add_indicators",
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
