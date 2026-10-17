"""Agent 工具函数 - 供 LLM tool-calling 使用

从各领域子模块聚合 TOOL_DEFINITIONS 和 TOOL_DISPATCH。
"""

from deeppulse.agent.tools.analysis import TOOL_DEFINITIONS as _analysis_defs
from deeppulse.agent.tools.analysis import TOOL_DISPATCH as _analysis_dispatch
from deeppulse.agent.tools.engine import TOOL_DEFINITIONS as _engine_defs
from deeppulse.agent.tools.engine import TOOL_DISPATCH as _engine_dispatch
from deeppulse.agent.tools.market import TOOL_DEFINITIONS as _market_defs
from deeppulse.agent.tools.market import TOOL_DISPATCH as _market_dispatch
from deeppulse.agent.tools.memory_tools import TOOL_DEFINITIONS as _memory_defs
from deeppulse.agent.tools.memory_tools import TOOL_DISPATCH as _memory_dispatch
from deeppulse.agent.tools.news import TOOL_DEFINITIONS as _news_defs
from deeppulse.agent.tools.news import TOOL_DISPATCH as _news_dispatch
from deeppulse.agent.tools.portfolio import TOOL_DEFINITIONS as _portfolio_defs
from deeppulse.agent.tools.portfolio import TOOL_DISPATCH as _portfolio_dispatch
from deeppulse.agent.tools.sentiment import TOOL_DEFINITIONS as _sentiment_defs
from deeppulse.agent.tools.sentiment import TOOL_DISPATCH as _sentiment_dispatch
from deeppulse.agent.tools.strategy import TOOL_DEFINITIONS as _strategy_defs
from deeppulse.agent.tools.strategy import TOOL_DISPATCH as _strategy_dispatch

# 聚合所有工具定义和分派
TOOL_DEFINITIONS = (
    _market_defs
    + _sentiment_defs
    + _news_defs
    + _memory_defs
    + _strategy_defs
    + _engine_defs
    + _analysis_defs
    + _portfolio_defs
)

TOOL_DISPATCH = {
    **_market_dispatch,
    **_sentiment_dispatch,
    **_news_dispatch,
    **_memory_dispatch,
    **_strategy_dispatch,
    **_engine_dispatch,
    **_analysis_dispatch,
    **_portfolio_dispatch,
}

# 状态变更（写）类工具：重复检测对它们不发"勿重复调用"提示——
# 用户可能主动要求再次执行（如反复加删自选），且写操作重试是合法的恢复手段
STATE_CHANGING_TOOLS = frozenset(
    {
        "add_to_watchlist",
        "remove_from_watchlist",
        "set_alert_rule",
        "record_trade",
        "save_review",
        "save_memory",
        "record_learning",
        "update_memory",
        "delete_memory",
        "save_session_context",
        "update_user_profile",
        "save_prediction",
        "verify_prediction",
    }
)

__all__ = ["TOOL_DEFINITIONS", "TOOL_DISPATCH", "STATE_CHANGING_TOOLS"]
