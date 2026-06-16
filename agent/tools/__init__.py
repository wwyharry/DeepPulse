"""Agent 工具函数 - 供 LLM tool-calling 使用

从各领域子模块聚合 TOOL_DEFINITIONS 和 TOOL_DISPATCH。
"""

from agent.tools.analysis import TOOL_DEFINITIONS as _analysis_defs
from agent.tools.analysis import TOOL_DISPATCH as _analysis_dispatch
from agent.tools.market import TOOL_DEFINITIONS as _market_defs
from agent.tools.market import TOOL_DISPATCH as _market_dispatch
from agent.tools.memory_tools import TOOL_DEFINITIONS as _memory_defs
from agent.tools.memory_tools import TOOL_DISPATCH as _memory_dispatch
from agent.tools.news import TOOL_DEFINITIONS as _news_defs
from agent.tools.news import TOOL_DISPATCH as _news_dispatch
from agent.tools.portfolio import TOOL_DEFINITIONS as _portfolio_defs
from agent.tools.portfolio import TOOL_DISPATCH as _portfolio_dispatch
from agent.tools.sentiment import TOOL_DEFINITIONS as _sentiment_defs
from agent.tools.sentiment import TOOL_DISPATCH as _sentiment_dispatch
from agent.tools.strategy import TOOL_DEFINITIONS as _strategy_defs
from agent.tools.strategy import TOOL_DISPATCH as _strategy_dispatch

# 聚合所有工具定义和分派
TOOL_DEFINITIONS = (
    _market_defs + _sentiment_defs + _news_defs + _memory_defs + _strategy_defs + _analysis_defs + _portfolio_defs
)

TOOL_DISPATCH = {
    **_market_dispatch,
    **_sentiment_dispatch,
    **_news_dispatch,
    **_memory_dispatch,
    **_strategy_dispatch,
    **_analysis_dispatch,
    **_portfolio_dispatch,
}

__all__ = ["TOOL_DEFINITIONS", "TOOL_DISPATCH"]
