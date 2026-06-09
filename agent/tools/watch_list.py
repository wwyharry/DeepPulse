import json

from agent.tools.decorator import tool

# ============ 自选股管理工具 ============


_watchlist = None


def _get_watchlist():
    global _watchlist
    if _watchlist is None:
        from agent.watchlist import WatchlistManager

        _watchlist = WatchlistManager()
    return _watchlist


@tool(
    "将股票添加到自选股列表，支持分组、设置目标价和止损价。",
    param_desc=[
            "股票代码",
            "分组名",
            "目标价位",
            "止损价位",
            "备注",
        ],
)
def add_to_watchlist(
    code: str, group: str = "默认", target_price: float = 0, stop_loss: float = 0, notes: str = ""
) -> str:
    """将股票添加到自选股列表。

    Args:
        code: 股票代码
        group: 分组名称，默认'默认'
        target_price: 目标价位（可选）
        stop_loss: 止损价位（可选）
        notes: 备注
    """
    wl = _get_watchlist()
    result = wl.add(
        code,
        group=group,
        target_price=float(target_price) if target_price else None,
        stop_loss=float(stop_loss) if stop_loss else None,
        notes=notes,
    )
    return json.dumps(result, ensure_ascii=False)


@tool(
    "从自选股列表移除股票。",
    param_desc=[
            "股票代码",
            "分组名",
        ],
)
def remove_from_watchlist(code: str, group: str = "默认") -> str:
    """从自选股列表移除。

    Args:
        code: 股票代码
        group: 分组名称
    """
    wl = _get_watchlist()
    result = wl.remove(code, group)
    return json.dumps(result, ensure_ascii=False)


@tool(
    "查看自选股列表及实时状态（含当前价、涨跌幅、目标价距离）。",
    param_desc=[
            "按分组筛选",
        ],
)
def list_watchlist(group: str = "") -> str:
    """查看自选股列表及实时状态（含当前价、涨跌幅、目标价距离）。

    Args:
        group: 按分组筛选（可选）
    """
    from agent.watchlist import format_watchlist_status

    wl = _get_watchlist()
    if group:
        codes = wl.get_codes(group)
        if not codes:
            return json.dumps({"message": f"分组'{group}'无自选股"}, ensure_ascii=False)
    result = format_watchlist_status(wl)
    return json.dumps({"status": "ok", "display": result}, ensure_ascii=False)


@tool(
    "为自选股设置告警规则，触发时发送桌面通知。支持价格突破/跌破、涨跌幅、放量、RSI区域等告警。",
    param_desc=[
            "股票代码",
            ("告警类型: price_above/price_below/pct_change/volume_spike/rsi_zone", {'enum': ["price_above", "price_below", "pct_change", "volume_spike", "rsi_zone"]}),
            "阈值",
            "RSI区域(oversold/overbought)",
            "放量倍数",
        ],
)
def set_alert_rule(code: str, rule_type: str, threshold: float = 0, zone: str = "", ratio: float = 3) -> str:
    """为自选股设置告警规则。触发时会发送桌面通知。

    Args:
        code: 股票代码
        rule_type: 告警类型 - price_above(价格突破)/price_below(价格跌破)/pct_change(涨跌幅超阈值)/volume_spike(放量)/rsi_zone(RSI区域)
        threshold: 阈值（price_above/below为价格，pct_change为百分比）
        zone: RSI区域（oversold=超卖/overbought=超买），仅rsi_zone类型使用
        ratio: 放量倍数，仅volume_spike类型使用
    """
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


@tool("立即检查自选股告警条件，返回触发的告警列表。")
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


@tool(
    "查看告警历史记录。",
    param_desc=[
            "返回条数",
        ],
)
def get_alert_history(limit: int = 20) -> str:
    """查看告警历史记录。"""
    wl = _get_watchlist()
    history = wl.get_alert_history(int(limit))
    return json.dumps({"count": len(history), "history": history}, ensure_ascii=False, default=str)