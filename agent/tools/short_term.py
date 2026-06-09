import json

from agent.tools.decorator import tool
from agent.strategy_loader import get_strategy_loader

_strategy_loader = get_strategy_loader()

# ============ 短线战法工具 ============


@tool(
    "搜索匹配当前分析场景的短线战法。当分析股票的技术指标、量价关系后，调用此工具查找适用的战法策略来指导操作建议。",
    param_desc=[
            "描述当前技术面状态，如 'RSI超卖+缩量下跌+均线支撑' 或 '放量突破MA20+MACD金叉'",
            "返回最匹配的战法数量，默认3",
        ],
)
def search_strategy(query: str, top_k: int = 3) -> str:
    """搜索匹配当前分析场景的短线战法。在分析股票时调用此工具查找适用的战法策略。

    Args:
        query: 描述当前分析场景，包含技术指标状态、市场信号等。
               例如 'RSI超卖+缩量下跌+均线支撑' 或 '放量突破MA20'
        top_k: 返回最匹配的战法数量，默认3
    """
    results = _strategy_loader.search(query, int(top_k))
    if not results:
        return json.dumps({"query": query, "results": [], "message": "未找到匹配的战法"}, ensure_ascii=False)
    return json.dumps({"query": query, "count": len(results), "results": results}, ensure_ascii=False)


@tool("列出所有可用的短线战法，了解系统中有哪些战法策略。")
def list_strategies() -> str:
    """列出所有可用的短线战法。用于了解当前系统中有哪些战法策略。"""
    strategies = _strategy_loader.list_strategies()
    return json.dumps(
        {
            "total": len(strategies),
            "strategies": strategies,
        },
        ensure_ascii=False,
    )


@tool(
    "获取指定战法的完整内容，包括详细的买入卖出条件、适用场景等。",
    param_desc=[
            "战法文件名，如 '01-放量突破战法.md'",
        ],
)
def get_strategy(filename: str) -> str:
    """获取指定战法的完整内容。

    Args:
        filename: 战法文件名（如 '01-放量突破战法.md'）
    """
    content = _strategy_loader.get_strategy_content(filename)
    if not content:
        return json.dumps({"error": f"未找到战法文件: {filename}"}, ensure_ascii=False)
    return json.dumps({"filename": filename, "content": content}, ensure_ascii=False)