"""LLM tool 函数包 — 所有工具通过 @tool 装饰器自动注册"""

from agent.tools.decorator import TOOL_DEFINITIONS, TOOL_DISPATCH, tool

from agent.tools import (
    backtesting,
    candle,
    dragon_tiger_rank,
    memory_system,
    misc,
    north_bound,
    short_term,
    trade_log,
    watch_list,
    util,
)
