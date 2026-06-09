import json

from agent.tools.decorator import tool

# ============ 交易日志工具 ============


_journal = None


def _get_journal():
    global _journal
    if _journal is None:
        from agent.journal import TradeJournal

        _journal = TradeJournal()
    return _journal


@tool(
    "记录一笔交易操作（买入/卖出/加仓/减仓），用于交易日志和复盘。",
    param_desc=[
            "股票代码",
            "操作: 买入/卖出/加仓/减仓",
            "成交价格",
            "成交数量",
            "股票名称",
            "交易理由",
            "使用的战法",
            "情绪状态",
        ],
)
def record_trade(
    code: str,
    action: str,
    price: float,
    shares: int,
    name: str = "",
    reason: str = "",
    strategy: str = "",
    emotion: str = "",
) -> str:
    """记录一笔交易操作。

    Args:
        code: 股票代码
        action: 操作 - 买入/卖出/加仓/减仓
        price: 成交价格
        shares: 成交数量
        name: 股票名称
        reason: 交易理由
        strategy: 使用的战法
        emotion: 交易时情绪（自信/犹豫/恐惧/贪婪/平静）
    """
    j = _get_journal()
    result = j.record_trade(
        code, action, float(price), int(shares), name=name, reason=reason, strategy=strategy, emotion=emotion
    )
    return json.dumps(result, ensure_ascii=False)


@tool("查看当前持仓状态（含实时价格和盈亏）。")
def view_portfolio() -> str:
    """查看当前持仓状态（含实时价格和盈亏）。"""
    from agent.journal import format_portfolio_status

    j = _get_journal()
    result = format_portfolio_status(j)
    return json.dumps({"display": result}, ensure_ascii=False)


@tool(
    "查看近期交易记录。",
    param_desc=[
            "查看天数",
        ],
)
def view_trade_history(days: int = 7) -> str:
    """查看近期交易记录。

    Args:
        days: 查看天数，默认7
    """
    from agent.journal import format_trade_history

    j = _get_journal()
    result = format_trade_history(j, int(days))
    return json.dumps({"display": result}, ensure_ascii=False)


@tool(
    "自动生成交易复盘报告（本周/本月/本季度），包含交易统计、战法使用、情绪分布。",
    param_desc=[
            "周期: week/month/quarter",
        ],
)
def generate_review(period: str = "week") -> str:
    """自动生成交易复盘报告。

    Args:
        period: 复盘周期 - week(本周)/month(本月)/quarter(本季度)
    """
    from agent.journal import generate_auto_review

    j = _get_journal()
    result = generate_auto_review(j, period)
    return json.dumps({"display": result}, ensure_ascii=False)


@tool(
    "保存复盘笔记（总结、做得好的、需改进的、关键教训）。",
    param_desc=[
            "复盘周期",
            "总结",
            "做得好的方面",
            "需要改进的方面",
            "关键教训",
        ],
)
def save_review(period: str, summary: str, what_went_well: str, what_to_improve: str, key_lessons: str) -> str:
    """保存复盘笔记。

    Args:
        period: 复盘周期
        summary: 总结
        what_went_well: 做得好的方面
        what_to_improve: 需要改进的方面
        key_lessons: 关键教训
    """
    j = _get_journal()
    result = j.save_review(period, summary, what_went_well, what_to_improve, key_lessons)
    return json.dumps(result, ensure_ascii=False)