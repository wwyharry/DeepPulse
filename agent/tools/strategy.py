"""短线战法工具"""

import json

from agent.tools._shared import get_strategy_loader


def search_strategy(query: str, top_k: int = 3) -> str:
    """搜索匹配当前分析场景的短线战法。在分析股票时调用此工具查找适用的战法策略。"""
    sl = get_strategy_loader()
    results = sl.search(query, int(top_k))
    if not results:
        return json.dumps({"query": query, "results": [], "message": "未找到匹配的战法"}, ensure_ascii=False)
    return json.dumps({"query": query, "count": len(results), "results": results}, ensure_ascii=False)


def list_strategies() -> str:
    """列出所有可用的短线战法。"""
    sl = get_strategy_loader()
    strategies = sl.list_strategies()
    return json.dumps({"total": len(strategies), "strategies": strategies}, ensure_ascii=False)


def get_strategy(filename: str) -> str:
    """获取指定战法的完整内容。"""
    sl = get_strategy_loader()
    content = sl.get_strategy_content(filename)
    if not content:
        return json.dumps({"error": f"未找到战法文件: {filename}"}, ensure_ascii=False)
    return json.dumps({"filename": filename, "content": content}, ensure_ascii=False)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_strategy",
            "description": "搜索匹配当前分析场景的短线战法。当分析股票的技术指标、量价关系后，调用此工具查找适用的战法策略来指导操作建议。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "描述当前技术面状态，如 'RSI超卖+缩量下跌+均线支撑' 或 '放量突破MA20+MACD金叉'",
                    },
                    "top_k": {"type": "integer", "description": "返回最匹配的战法数量，默认3", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_strategies",
            "description": "列出所有可用的短线战法，了解系统中有哪些战法策略。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_strategy",
            "description": "获取指定战法的完整内容，包括详细的买入卖出条件、适用场景等。",
            "parameters": {
                "type": "object",
                "properties": {"filename": {"type": "string", "description": "战法文件名，如 '01-放量突破战法.md'"}},
                "required": ["filename"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "search_strategy": search_strategy,
    "list_strategies": list_strategies,
    "get_strategy": get_strategy,
}
