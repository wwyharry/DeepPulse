"""新闻搜索工具"""

import json

from deeppulse.agent.news import search_eastmoney_news, search_market_hot_news, search_stock_news


def search_news(query: str, num: int = 8) -> str:
    """搜索财经新闻热点"""
    results = search_eastmoney_news(keyword=query, num=int(num))
    if not results:
        return json.dumps({"query": query, "results": [], "message": "未搜索到相关新闻"}, ensure_ascii=False)
    if "error" in results[0]:
        return json.dumps({"query": query, "error": results[0]["error"]}, ensure_ascii=False)
    return json.dumps({"query": query, "count": len(results), "results": results}, ensure_ascii=False)


def stock_news(code_or_name: str, num: int = 8) -> str:
    """搜索某只股票的相关新闻"""
    result = search_stock_news(code_or_name, num=int(num))
    return json.dumps(result, ensure_ascii=False)


def market_hot_news(num: int = 10) -> str:
    """获取A股市场今日热点新闻"""
    result = search_market_hot_news(num=int(num))
    return json.dumps(result, ensure_ascii=False)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "搜索财经新闻热点。可用于搜索个股相关新闻、行业新闻或宏观财经消息。数据来源为东方财富快讯（关键词过滤）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，如股票名称、行业、宏观事件等"},
                    "num": {"type": "integer", "description": "返回条数，默认8", "default": 8},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_news",
            "description": "搜索某只股票的相关新闻（东方财富快讯关键词搜索）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code_or_name": {"type": "string", "description": "股票代码或名称，如'000001'或'平安银行'"},
                    "num": {"type": "integer", "description": "返回条数，默认8", "default": 8},
                },
                "required": ["code_or_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "market_hot_news",
            "description": "获取A股市场今日热点新闻，综合东方财富、新浪财经多源聚合。适合了解市场整体动态。",
            "parameters": {
                "type": "object",
                "properties": {"num": {"type": "integer", "description": "每个源返回条数，默认10", "default": 10}},
                "required": [],
            },
        },
    },
]

TOOL_DISPATCH = {
    "search_news": search_news,
    "stock_news": stock_news,
    "market_hot_news": market_hot_news,
}
