"""投资组合工具 - 龙虎榜、北向资金、自选股、交易日志"""

import json

_watchlist = None
_journal = None


def _get_watchlist():
    global _watchlist
    if _watchlist is None:
        from agent.watchlist import WatchlistManager

        _watchlist = WatchlistManager()
    return _watchlist


def _get_journal():
    global _journal
    if _journal is None:
        from agent.journal import TradeJournal

        _journal = TradeJournal()
    return _journal


def dragon_tiger_list(trade_date: str = None) -> str:
    """获取龙虎榜数据（当日上榜个股、净买入额、上榜原因）。"""
    from agent.datalink import get_dragon_tiger

    result = get_dragon_tiger(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


def northbound_flow() -> str:
    """获取北向资金（沪股通+深股通）近5日净流入数据和趋势判断。"""
    from agent.datalink import get_northbound_flow

    result = get_northbound_flow()
    return json.dumps(result, ensure_ascii=False, default=str)


def sector_fund_flow() -> str:
    """获取板块资金流向（行业板块+概念板块主力净流入排行Top10）。"""
    from agent.datalink import get_sector_fund_flow

    result = get_sector_fund_flow()
    return json.dumps(result, ensure_ascii=False, default=str)


def stock_dragon_tiger(code: str, days: int = 30) -> str:
    """查询个股龙虎榜历史明细，查看游资和机构席位动向。"""
    from agent.datalink import get_stock_dragon_tiger_detail

    result = get_stock_dragon_tiger_detail(code, int(days))
    return json.dumps(result, ensure_ascii=False, default=str)


def add_to_watchlist(
    code: str, group: str = "默认", target_price: float = 0, stop_loss: float = 0, notes: str = ""
) -> str:
    """将股票添加到自选股列表。"""
    wl = _get_watchlist()
    result = wl.add(
        code,
        group=group,
        target_price=float(target_price) if target_price else None,
        stop_loss=float(stop_loss) if stop_loss else None,
        notes=notes,
    )
    return json.dumps(result, ensure_ascii=False)


def remove_from_watchlist(code: str, group: str = "默认") -> str:
    """从自选股列表移除。"""
    wl = _get_watchlist()
    result = wl.remove(code, group)
    return json.dumps(result, ensure_ascii=False)


def list_watchlist(group: str = "") -> str:
    """查看自选股列表及实时状态（含当前价、涨跌幅、目标价距离）。"""
    from agent.watchlist import format_watchlist_status

    wl = _get_watchlist()
    if group:
        codes = wl.get_codes(group)
        if not codes:
            return json.dumps({"message": f"分组'{group}'无自选股"}, ensure_ascii=False)
    result = format_watchlist_status(wl)
    return json.dumps({"status": "ok", "display": result}, ensure_ascii=False)


def set_alert_rule(code: str, rule_type: str, threshold: float = 0, zone: str = "", ratio: float = 3) -> str:
    """为自选股设置告警规则。触发时会发送桌面通知。"""
    wl = _get_watchlist()
    params = {}
    if rule_type in ("price_above", "price_below"):
        params["threshold"] = float(threshold)
    elif rule_type == "pct_change":
        params["threshold"] = float(threshold)
    elif rule_type == "rsi_zone":
        params["zone"] = zone or "oversold"
    elif rule_type == "volume_spike":
        params["ratio"] = float(ratio)
    result = wl.add_alert_rule(code, rule_type, params)
    return json.dumps(result, ensure_ascii=False)


def check_alerts() -> str:
    """立即检查自选股告警条件，返回触发的告警列表。"""
    from agent.watchlist import MarketMonitor

    wl = _get_watchlist()
    monitor = MarketMonitor(wl)
    alerts = monitor.check_now()
    if not alerts:
        return json.dumps({"message": "无告警触发", "alerts": []}, ensure_ascii=False)
    alert_list = [
        {"code": a.code, "name": a.name, "message": a.message, "price": a.price, "time": a.triggered_at} for a in alerts
    ]
    return json.dumps({"triggered": len(alerts), "alerts": alert_list}, ensure_ascii=False)


def get_alert_history(limit: int = 20) -> str:
    """查看告警历史记录。"""
    wl = _get_watchlist()
    history = wl.get_alert_history(int(limit))
    return json.dumps({"count": len(history), "history": history}, ensure_ascii=False, default=str)


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
    """记录一笔交易操作。"""
    j = _get_journal()
    result = j.record_trade(
        code, action, float(price), int(shares), name=name, reason=reason, strategy=strategy, emotion=emotion
    )
    return json.dumps(result, ensure_ascii=False)


def view_portfolio() -> str:
    """查看当前持仓状态（含实时价格和盈亏）。"""
    from agent.journal import format_portfolio_status

    j = _get_journal()
    result = format_portfolio_status(j)
    return json.dumps({"display": result}, ensure_ascii=False)


def view_trade_history(days: int = 7) -> str:
    """查看近期交易记录。"""
    from agent.journal import format_trade_history

    j = _get_journal()
    result = format_trade_history(j, int(days))
    return json.dumps({"display": result}, ensure_ascii=False)


def generate_review(period: str = "week") -> str:
    """自动生成交易复盘报告。"""
    from agent.journal import generate_auto_review

    j = _get_journal()
    result = generate_auto_review(j, period)
    return json.dumps({"display": result}, ensure_ascii=False)


def save_review(period: str, summary: str, what_went_well: str, what_to_improve: str, key_lessons: str) -> str:
    """保存复盘笔记。"""
    j = _get_journal()
    result = j.save_review(period, summary, what_went_well, what_to_improve, key_lessons)
    return json.dumps(result, ensure_ascii=False)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "dragon_tiger_list",
            "description": "获取龙虎榜数据（当日上榜个股、净买入额、上榜原因）。",
            "parameters": {
                "type": "object",
                "properties": {"trade_date": {"type": "string", "description": "交易日期 YYYYMMDD，默认今天"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "northbound_flow",
            "description": "获取北向资金（沪股通+深股通）近5日净流入数据和趋势判断。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sector_fund_flow",
            "description": "获取板块资金流向（行业板块+概念板块主力净流入排行Top10）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_dragon_tiger",
            "description": "查询个股龙虎榜历史明细，查看游资和机构席位动向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "days": {"type": "integer", "description": "查询天数，默认30", "default": 30},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_watchlist",
            "description": "将股票添加到自选股列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "group": {"type": "string", "description": "分组名称，默认'默认'", "default": "默认"},
                    "target_price": {"type": "number", "description": "目标价位（可选）", "default": 0},
                    "stop_loss": {"type": "number", "description": "止损价位（可选）", "default": 0},
                    "notes": {"type": "string", "description": "备注", "default": ""},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_watchlist",
            "description": "从自选股列表移除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "group": {"type": "string", "description": "分组名称", "default": "默认"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_watchlist",
            "description": "查看自选股列表及实时状态（含当前价、涨跌幅、目标价距离）。",
            "parameters": {
                "type": "object",
                "properties": {"group": {"type": "string", "description": "按分组筛选（可选）", "default": ""}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_alert_rule",
            "description": "为自选股设置告警规则。触发时会发送桌面通知。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "rule_type": {
                        "type": "string",
                        "description": "告警类型 - price_above(价格突破)/price_below(价格跌破)/pct_change(涨跌幅超阈值)/volume_spike(放量)/rsi_zone(RSI区域)",
                    },
                    "threshold": {
                        "type": "number",
                        "description": "阈值（price_above/below为价格，pct_change为百分比）",
                        "default": 0,
                    },
                    "zone": {
                        "type": "string",
                        "description": "RSI区域（oversold=超卖/overbought=超买），仅rsi_zone类型使用",
                        "default": "",
                    },
                    "ratio": {"type": "number", "description": "放量倍数，仅volume_spike类型使用", "default": 3},
                },
                "required": ["code", "rule_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_alerts",
            "description": "立即检查自选股告警条件，返回触发的告警列表。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alert_history",
            "description": "查看告警历史记录。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "返回条数，默认20", "default": 20}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_trade",
            "description": "记录一笔交易操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "action": {"type": "string", "description": "操作 - 买入/卖出/加仓/减仓"},
                    "price": {"type": "number", "description": "成交价格"},
                    "shares": {"type": "integer", "description": "成交数量"},
                    "name": {"type": "string", "description": "股票名称", "default": ""},
                    "reason": {"type": "string", "description": "交易理由", "default": ""},
                    "strategy": {"type": "string", "description": "使用的战法", "default": ""},
                    "emotion": {
                        "type": "string",
                        "description": "交易时情绪（自信/犹豫/恐惧/贪婪/平静）",
                        "default": "",
                    },
                },
                "required": ["code", "action", "price", "shares"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_portfolio",
            "description": "查看当前持仓状态（含实时价格和盈亏）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_trade_history",
            "description": "查看近期交易记录。",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "查看天数，默认7", "default": 7}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_review",
            "description": "自动生成交易复盘报告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "复盘周期 - week(本周)/month(本月)/quarter(本季度)",
                        "default": "week",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_review",
            "description": "保存复盘笔记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "复盘周期"},
                    "summary": {"type": "string", "description": "总结"},
                    "what_went_well": {"type": "string", "description": "做得好的方面"},
                    "what_to_improve": {"type": "string", "description": "需要改进的方面"},
                    "key_lessons": {"type": "string", "description": "关键教训"},
                },
                "required": ["period", "summary", "what_went_well", "what_to_improve", "key_lessons"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "dragon_tiger_list": dragon_tiger_list,
    "northbound_flow": northbound_flow,
    "sector_fund_flow": sector_fund_flow,
    "stock_dragon_tiger": stock_dragon_tiger,
    "add_to_watchlist": add_to_watchlist,
    "remove_from_watchlist": remove_from_watchlist,
    "list_watchlist": list_watchlist,
    "set_alert_rule": set_alert_rule,
    "check_alerts": check_alerts,
    "get_alert_history": get_alert_history,
    "record_trade": record_trade,
    "view_portfolio": view_portfolio,
    "view_trade_history": view_trade_history,
    "generate_review": generate_review,
    "save_review": save_review,
}
