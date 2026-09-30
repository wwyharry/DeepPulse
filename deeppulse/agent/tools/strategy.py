"""短线战法工具 - 调用策略平台模块"""

import json

from deeppulse.agent.tools._shared import parse_positive_int
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
    use_dynamic_exit: bool = True,
    exit_strategy_preset: str = "balanced",
) -> str:
    """对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤等指标。"""
    from deeppulse.strategy.backtest import backtest_stock as _backtest_stock

    days = parse_positive_int(days, "days")
    if use_dynamic_exit is None:
        use_dynamic_exit = True
    try:
        result = _backtest_stock(
            code,
            strategy=strategy,
            days=days,
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            max_hold=int(max_hold),
            use_dynamic_exit=use_dynamic_exit,
            exit_strategy_preset=exit_strategy_preset,
        )
        return json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"回测失败: {e}"}, ensure_ascii=False)


def backtest_multi_stock(strategy: str = "macd_cross", codes: str = "", days: int = 500, top_n: int = 10) -> str:
    """在多只股票上回测同一策略，汇总统计胜率和收益率。

    codes 为空时从全市场随机抽样最多 100 只（固定种子 random_state=42，可复现）；
    avg_return/median_return/avg_win_rate/positive_rate 仅统计有交易的股票，
    零交易样本在 stocks_without_trades 中披露，另含 coverage/positive_rate_all 等口径字段。
    """
    from deeppulse.strategy.backtest import backtest_strategy_multi_stock

    days = parse_positive_int(days, "days")
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        result = backtest_strategy_multi_stock(strategy=strategy, codes=code_list, days=days, top_n=int(top_n))
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
                    "days": {"type": "integer", "description": "回测天数", "default": 500, "minimum": 1},
                    "stop_loss": {
                        "type": "number",
                        "description": "止损比例（仅在 use_dynamic_exit=false 时使用）",
                        "default": 0.05,
                    },
                    "take_profit": {
                        "type": "number",
                        "description": "止盈比例（仅在 use_dynamic_exit=false 时使用）",
                        "default": 0.10,
                    },
                    "max_hold": {
                        "type": "integer",
                        "description": "最大持仓天数（仅在 use_dynamic_exit=false 时使用）",
                        "default": 20,
                    },
                    "use_dynamic_exit": {
                        "type": "boolean",
                        "description": "是否使用动态退出策略（默认开启）。关闭后才使用 stop_loss/take_profit/max_hold",
                        "default": True,
                    },
                    "exit_strategy_preset": {
                        "type": "string",
                        "description": "动态退出策略预设，仅在 use_dynamic_exit=true 时使用",
                        "default": "balanced",
                        "enum": ["conservative", "balanced", "aggressive"],
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_multi_stock",
            "description": "在多只股票上回测同一策略，汇总统计胜率和收益率。codes 为空时从全市场随机抽样最多 100 只（固定种子，可复现）；avg_return/avg_win_rate/positive_rate 仅统计有交易的股票，零交易样本在 stocks_without_trades 中披露。",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy": {"type": "string", "description": "策略名", "default": "macd_cross"},
                    "codes": {
                        "type": "string",
                        "description": "逗号分隔的股票代码；为空时从全市场随机抽样最多 100 只（固定种子 random_state=42，抽样可复现）",
                    },
                    "days": {"type": "integer", "description": "回测天数", "default": 500, "minimum": 1},
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
