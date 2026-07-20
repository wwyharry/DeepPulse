"""短线战法工具 - 调用策略平台模块"""

import json

from deeppulse.strategy.repository import get_repository


def search_strategy(query: str, top_k: int = 3) -> str:
    """搜索匹配当前分析场景的短线战法。在分析股票时调用此工具查找适用的战法策略。"""
    repo = get_repository()
    results = repo.search(query, int(top_k))
    if not results:
        return json.dumps({"query": query, "results": [], "message": "未找到匹配的战法"}, ensure_ascii=False)
    return json.dumps({"query": query, "count": len(results), "results": results}, ensure_ascii=False)


def list_strategies() -> str:
    """列出所有可用的短线战法。"""
    repo = get_repository()
    strategies = repo.load_all()
    strategy_list = [
        {
            "id": s.id,
            "name": s.name,
            "file": s.filename,
            "tags": s.tags,
            "keywords": s.conditions.get("keywords", [])[:5],
        }
        for s in strategies
    ]
    return json.dumps({"total": len(strategy_list), "strategies": strategy_list}, ensure_ascii=False)


def get_strategy(filename: str) -> str:
    """获取指定战法的完整内容。"""
    repo = get_repository()
    strategy = repo.get_strategy_by_filename(filename)
    if not strategy:
        return json.dumps({"error": f"未找到战法文件: {filename}"}, ensure_ascii=False)
    return json.dumps({"filename": filename, "content": strategy.content}, ensure_ascii=False)


def backtest_stock(
    code: str,
    strategy: str = "macd_cross",
    days: int = 500,
    stop_loss: float = 0.05,
    take_profit: float = 0.10,
    max_hold: int = 20,
) -> str:
    """对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤等指标。"""
    from deeppulse.strategy.backtest import backtest_stock as _backtest_stock

    try:
        result = _backtest_stock(
            code,
            strategy=strategy,
            days=int(days),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            max_hold=int(max_hold),
        )
        return json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"回测失败: {e}"}, ensure_ascii=False)


def backtest_multi_stock(strategy: str = "macd_cross", codes: str = "", days: int = 500, top_n: int = 10) -> str:
    """在多只股票上回测同一策略，汇总统计胜率和收益率。"""
    from deeppulse.strategy.backtest import backtest_strategy_multi_stock

    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        result = backtest_strategy_multi_stock(strategy=strategy, codes=code_list, days=int(days), top_n=int(top_n))
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"批量回测失败: {e}"}, ensure_ascii=False)


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
    {
        "type": "function",
        "function": {
            "name": "backtest_stock",
            "description": "对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤等指标。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码，如 '600519'"},
                    "strategy": {
                        "type": "string",
                        "description": "策略名（ma_cross / macd_cross / rsi_oversold / volume_breakout / boll_bounce / kdj_cross / multi_confirm）",
                        "default": "macd_cross",
                    },
                    "days": {"type": "integer", "description": "回测天数", "default": 500},
                    "stop_loss": {"type": "number", "description": "止损比例", "default": 0.05},
                    "take_profit": {"type": "number", "description": "止盈比例", "default": 0.10},
                    "max_hold": {"type": "integer", "description": "最大持仓天数", "default": 20},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_multi_stock",
            "description": "在多只股票上回测同一策略，汇总统计胜率和收益率。",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy": {"type": "string", "description": "策略名", "default": "macd_cross"},
                    "codes": {"type": "string", "description": "逗号分隔的股票代码"},
                    "days": {"type": "integer", "description": "回测天数", "default": 500},
                    "top_n": {"type": "integer", "description": "返回表现最好的N只", "default": 10},
                },
                "required": [],
            },
        },
    },
]

TOOL_DISPATCH = {
    "search_strategy": search_strategy,
    "list_strategies": list_strategies,
    "get_strategy": get_strategy,
    "backtest_stock": backtest_stock,
    "backtest_multi_stock": backtest_multi_stock,
}
