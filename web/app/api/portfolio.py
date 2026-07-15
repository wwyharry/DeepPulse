"""组合管理 REST API"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class WatchlistAdd(BaseModel):
    code: str
    group_name: str = "默认"


class TradeRecord(BaseModel):
    code: str
    action: str  # buy / sell
    price: float
    shares: int
    reason: str = ""
    strategy: str = ""


# ── 自选股 ──────────────────────────────────────────


@router.get("/watchlist")
async def list_watchlist(group: str = Query(None)):
    """自选股列表"""
    import math

    from deeppulse.agent.watchlist import WatchlistManager

    wl = WatchlistManager()
    items = wl.list(group=group)
    # 处理 NaN 值，使其 JSON 兼容
    for item in items:
        for key, value in item.items():
            if isinstance(value, float) and math.isnan(value):
                item[key] = None
            elif hasattr(value, "isoformat"):
                item[key] = str(value)
    return {"watchlist": items}


@router.post("/watchlist")
async def add_to_watchlist(body: WatchlistAdd):
    """添加自选股"""
    from deeppulse.agent.watchlist import WatchlistManager

    wl = WatchlistManager()
    result = wl.add(body.code, group=body.group_name)
    return result


@router.delete("/watchlist/{code}")
async def remove_from_watchlist(code: str):
    """移除自选股"""
    from deeppulse.agent.watchlist import WatchlistManager

    wl = WatchlistManager()
    wl.remove(code)
    return {"status": "removed", "code": code}


# ── 持仓 ────────────────────────────────────────────


@router.get("/portfolio")
async def get_portfolio():
    """当前持仓"""
    from deeppulse.agent.journal import TradeJournal, format_portfolio_status

    journal = TradeJournal()
    result = format_portfolio_status(journal)
    return {"portfolio": result}


# ── 交易记录 ────────────────────────────────────────


@router.get("/trades")
async def list_trades(days: int = Query(30, ge=1, le=365)):
    """交易历史"""
    from deeppulse.agent.journal import TradeJournal, format_trade_history

    journal = TradeJournal()
    result = format_trade_history(journal, days=days)
    return {"trades": result}


@router.post("/trades")
async def record_trade(body: TradeRecord):
    """记录交易"""
    from deeppulse.agent.journal import TradeJournal

    journal = TradeJournal()
    result = journal.record_trade(
        code=body.code,
        action=body.action,
        price=body.price,
        shares=body.shares,
        reason=body.reason,
        strategy=body.strategy,
    )
    return result


# ── 预警 ────────────────────────────────────────────


@router.get("/alerts")
async def get_alerts():
    """预警记录"""
    from deeppulse.agent.watchlist import WatchlistManager

    wl = WatchlistManager()
    alerts = wl.get_alert_history()
    return {"alerts": alerts}
