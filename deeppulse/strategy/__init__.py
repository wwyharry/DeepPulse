"""DeepPulse 策略层 - Agent 面向的计算引擎（只出数，不说话）

对外提供：策略规格/因子库（spec、factor_library）、回测与蒙特卡洛（backtest）、
网格校准（optimizer）、退出策略（exit_strategies）、战法仓库（repository）
与数据模型（models）。策略构思由 Agent 完成，本层只做计算。
"""

from deeppulse.strategy.exit_strategies import (
    PRESET_EXIT_STRATEGIES,
    DynamicExitEngine,
    ExitStrategy,
    get_preset_strategy,
    list_preset_strategies,
)
from deeppulse.strategy.models import (
    BacktestResult,
    Portfolio,
    Signal,
    Strategy,
    StrategyVersion,
)
from deeppulse.strategy.repository import StrategyRepository

__all__ = [
    "Strategy",
    "StrategyVersion",
    "BacktestResult",
    "Signal",
    "Portfolio",
    "StrategyRepository",
    "ExitStrategy",
    "DynamicExitEngine",
    "PRESET_EXIT_STRATEGIES",
    "get_preset_strategy",
    "list_preset_strategies",
]
