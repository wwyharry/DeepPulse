"""投资组合工具 - 龙虎榜、板块资金流、自选股、交易日志"""

import json
import math

from deeppulse.agent.tools._shared import norm_trade_date, parse_positive_int

_watchlist = None
_journal = None


def _get_watchlist():
    global _watchlist
    if _watchlist is None:
        from deeppulse.agent.watchlist import WatchlistManager

        _watchlist = WatchlistManager()
    return _watchlist


def _get_journal():
    global _journal
    if _journal is None:
        from deeppulse.agent.journal import TradeJournal

        _journal = TradeJournal()
    return _journal


def dragon_tiger_list(trade_date: str = None) -> str:
    """获取龙虎榜数据（当日上榜个股、净买入额、上榜原因）。"""
    from deeppulse.agent.datalink import get_dragon_tiger

    nd = norm_trade_date(trade_date)
    if nd is None:
        return json.dumps(
            {
                "error": f"trade_date 格式错误: {trade_date!r}，应为 YYYYMMDD（如 20260930），也支持 2026-09-30 等常见写法"
            },
            ensure_ascii=False,
        )
    result = get_dragon_tiger(nd)
    return json.dumps(result, ensure_ascii=False, default=str)


def sector_fund_flow() -> str:
    """获取板块资金流向（多数据源，自动降级）。"""
    from deeppulse.agent.fund_flow import get_sector_fund_flow

    result = get_sector_fund_flow()
    return json.dumps(result, ensure_ascii=False, default=str)


def stock_dragon_tiger(code: str, days: int = 30) -> str:
    """查询个股龙虎榜历史明细，查看游资和机构席位动向。"""
    from deeppulse.agent.datalink import get_stock_dragon_tiger_detail

    result = get_stock_dragon_tiger_detail(code, int(days))
    return json.dumps(result, ensure_ascii=False, default=str)


def add_to_watchlist(
    code: str, group: str = "默认", target_price: float = 0, stop_loss: float = 0, notes: str = ""
) -> str:
    """将股票添加到自选股列表（code 可传6位代码或股票名称）。"""
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
    """从自选股列表移除（code 可传6位代码或股票名称；返回如实反映是否真的删掉了）。"""
    wl = _get_watchlist()
    result = wl.remove(code, group)
    return json.dumps(result, ensure_ascii=False)


class _GroupScopedWatchlist:
    """把 WatchlistManager 的查询限定到指定分组，供 format_watchlist_status 渲染分组视图。

    format_watchlist_status 本身不接受 group 参数（在 watchlist.py，本组不可改），
    这里用鸭子类型代理实现分组过滤，避免 list_watchlist(group=...) 输出全部分组。
    """

    def __init__(self, mgr, group):
        self._mgr = mgr
        self._group = group

    def get_codes(self, group=None):
        return self._mgr.get_codes(self._group)

    def list(self, group=None):
        return self._mgr.list(self._group)


def list_watchlist(group: str = "") -> str:
    """查看自选股列表及实时状态（含当前价、涨跌幅、目标价距离）。"""
    from deeppulse.agent.watchlist import format_watchlist_status

    wl = _get_watchlist()
    if group:
        codes = wl.get_codes(group)
        if not codes:
            return json.dumps({"message": f"分组'{group}'无自选股"}, ensure_ascii=False)
    result = format_watchlist_status(_GroupScopedWatchlist(wl, group) if group else wl)
    return json.dumps({"status": "ok", "display": result}, ensure_ascii=False)


def set_alert_rule(code: str, rule_type: str, threshold: float = 0, zone: str = "", ratio: float = 3) -> str:
    """为自选股设置告警规则。触发时会发送桌面通知。"""
    valid_types = ("price_above", "price_below", "pct_change", "volume_spike", "rsi_zone")
    if rule_type not in valid_types:
        return json.dumps({"error": f"未知rule_type: {rule_type!r}，可选 {'/'.join(valid_types)}"}, ensure_ascii=False)

    def _positive(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v) or v <= 0:
            return None
        return v

    params = {}
    if rule_type in ("price_above", "price_below", "pct_change"):
        # 阈值必须为正数：price_above 阈值<=0 会恒触发，price_below 阈值<=0 永不触发
        t = _positive(threshold)
        if t is None:
            return json.dumps({"error": f"threshold 必须为正数，收到 {threshold!r}"}, ensure_ascii=False)
        params["threshold"] = t
    elif rule_type == "rsi_zone":
        z = str(zone or "oversold").strip().lower()
        if z not in ("oversold", "overbought"):
            return json.dumps({"error": f"zone 必须为 oversold 或 overbought，收到 {zone!r}"}, ensure_ascii=False)
        params["zone"] = z
    elif rule_type == "volume_spike":
        r = _positive(ratio)
        if r is None:
            return json.dumps({"error": f"ratio 必须为正数，收到 {ratio!r}"}, ensure_ascii=False)
        params["ratio"] = r

    # 监控引擎只遍历自选股，非自选股上的规则永不触发（死规则），先拒绝
    from deeppulse.src.query import StockQuery

    info, candidates = StockQuery().resolve_stock(code)
    if info is None:
        return json.dumps(
            {"error": f"无法识别股票 {code!r}：请使用6位股票代码或准确名称", "candidates": candidates},
            ensure_ascii=False,
        )
    code = info["code"]
    wl = _get_watchlist()
    if code not in wl.get_codes():
        return json.dumps({"error": f"{code} 不在自选股中，请先 add_to_watchlist 再设置告警"}, ensure_ascii=False)
    result = wl.add_alert_rule(code, rule_type, params)
    return json.dumps(result, ensure_ascii=False)


def check_alerts() -> str:
    """立即检查自选股告警条件，返回触发的告警列表，并尝试发送桌面通知。"""
    from deeppulse.agent.watchlist import MarketMonitor, send_desktop_notification

    wl = _get_watchlist()
    monitor = MarketMonitor(wl)
    monitor.on_alert(lambda a: send_desktop_notification(a.name, a.message))
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
    """记录一笔交易操作（code 可传6位代码或股票名称；action 支持 买入/卖出/加仓/减仓/清仓）。"""
    # 入口校验必须在写库之前，避免无效交易留下 journal 流水
    if isinstance(price, bool):
        return json.dumps({"error": f"price 必须为正数，收到 {price!r}"}, ensure_ascii=False)
    try:
        price_v = float(price)
    except (TypeError, ValueError):
        return json.dumps({"error": f"price 必须是数字，收到 {price!r}"}, ensure_ascii=False)
    if not math.isfinite(price_v) or price_v <= 0:
        return json.dumps({"error": f"price 必须为正数，收到 {price!r}"}, ensure_ascii=False)

    # 清仓按实际持仓全量卖出，shares 随意（journal 端覆盖）；其余 action 必须为正整数
    is_liquidate = str(action).strip().casefold() in ("清仓", "平仓", "止损", "止盈", "liquidate", "clear", "close")
    if is_liquidate:
        shares_v = 1  # 占位；journal 以实际持仓覆盖
    else:
        if isinstance(shares, bool):
            return json.dumps({"error": f"shares 必须为正整数，收到 {shares!r}"}, ensure_ascii=False)
        if isinstance(shares, float) and not shares.is_integer():
            return json.dumps({"error": f"shares 必须为正整数，收到 {shares!r}"}, ensure_ascii=False)
        try:
            shares_v = parse_positive_int(shares, "shares")
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    # 持仓校验、股票归一（名称→代码）、流水写入统一在 journal 端完成：
    # 卖出超持仓/无持仓等拒绝发生在任何写表之前，不会留下脏流水
    j = _get_journal()
    result = j.record_trade(
        code, action, price_v, shares_v, name=name, reason=reason, strategy=strategy, emotion=emotion
    )
    return json.dumps(result, ensure_ascii=False)


def view_portfolio() -> str:
    """查看当前持仓状态（含实时价格和盈亏）。"""
    from deeppulse.agent.journal import format_portfolio_status

    j = _get_journal()
    result = format_portfolio_status(j)
    return json.dumps({"display": result}, ensure_ascii=False)


def view_trade_history(days: int = 7) -> str:
    """查看近期交易记录。"""
    from deeppulse.agent.journal import format_trade_history

    j = _get_journal()
    result = format_trade_history(j, int(days))
    return json.dumps({"display": result}, ensure_ascii=False)


def generate_review(period: str = "week") -> str:
    """自动生成交易复盘报告。"""
    from deeppulse.agent.journal import generate_auto_review

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
            "name": "sector_fund_flow",
            "description": "获取板块资金流向（行业板块主力净流入排行Top10）。",
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
                    "days": {"type": "integer", "description": "查询天数，默认30", "default": 30, "minimum": 1},
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
                    "code": {"type": "string", "description": "6位股票代码或股票名称"},
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
            "description": "从自选股列表移除。返回如实反映是否真的删除（未找到/分组不符会返回 error 及实际分组）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码或股票名称"},
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
                        "description": "阈值（price_above/below为价格，pct_change为百分比），必须为正数",
                        "default": 0,
                    },
                    "zone": {
                        "type": "string",
                        "description": "RSI区域（oversold=超卖/overbought=超买），仅rsi_zone类型使用",
                        "default": "",
                    },
                    "ratio": {
                        "type": "number",
                        "description": "放量倍数（必须为正数），仅volume_spike类型使用",
                        "default": 3,
                    },
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
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认20", "default": 20, "minimum": 1}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_trade",
            "description": "记录一笔交易操作（写入交易流水并更新持仓）。用户说清仓/止损/止盈时用 action=清仓。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码或股票名称"},
                    "action": {
                        "type": "string",
                        "description": "操作 - 买入/卖出/加仓/减仓/清仓（清仓=按实际持仓全量卖出）",
                        "enum": ["买入", "卖出", "加仓", "减仓", "清仓"],
                    },
                    "price": {"type": "number", "description": "成交价格"},
                    "shares": {"type": "integer", "description": "成交数量（清仓时可任意填，按实际持仓执行）"},
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
                "properties": {
                    "days": {"type": "integer", "description": "查看天数，默认7", "default": 7, "minimum": 1}
                },
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
