"""持仓与交易流水 —— 现价/盈亏由 fetch_quotes 计算，缺价为 null"""

import asyncio

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from web.api.common import enc, error_response

router = APIRouter()


class TradeIn(BaseModel):
    code: str
    action: str
    price: float
    shares: int
    name: str | None = None
    reason: str | None = None
    strategy: str | None = None
    emotion: str | None = None


@router.get("/api/portfolio")
async def portfolio_get(request: Request):
    """持仓列表（price/profit 按现价计；缺价 → null）"""
    s = request.app.state.singletons

    def _load():
        rows = s.journal.get_portfolio()
        codes = [str(r.get("code") or "") for r in rows]
        quotes = s.fetch_quotes(codes)
        return rows, quotes

    rows, quotes = await asyncio.to_thread(_load)
    items = []
    for p in rows:
        code = str(p.get("code") or "")
        q = quotes.get(code)
        price = q.current if q and q.current is not None else None
        shares = p.get("shares") or 0
        avg_cost = p.get("avg_cost") or 0
        profit = profit_pct = None
        if price is not None:
            profit = (price - avg_cost) * shares
            profit_pct = (price - avg_cost) / avg_cost * 100 if avg_cost else 0.0
        items.append(
            {
                "code": code,
                "name": p.get("name") or code,
                "shares": shares,
                "avg_cost": avg_cost,
                "price": price,
                "profit": profit,
                "profit_pct": profit_pct,
            }
        )
    return {"items": enc(items)}


@router.get("/api/trades")
async def trades_list(request: Request, days: int = Query(7, ge=1, le=36500)):
    """近期交易流水（pandas.Timestamp 序列化不炸）"""
    s = request.app.state.singletons
    trades = await asyncio.to_thread(s.journal.get_trade_history, None, days)
    return {"trades": enc(trades)}


@router.post("/api/trades")
async def trades_add(request: Request, body: TradeIn):
    """记录交易；action 别名 journal 端归一；清仓时 shares 任意（按实际持仓）"""
    s = request.app.state.singletons
    result = await asyncio.to_thread(
        s.journal.record_trade,
        body.code,
        body.action,
        body.price,
        body.shares,
        body.name or "",
        "long",
        body.reason or "",
        body.strategy or "",
        body.emotion or "",
        "",
    )
    if not isinstance(result, dict) or result.get("status") != "ok":
        detail = (result or {}).get("message") or "记录交易失败"
        extra = {}
        if (result or {}).get("candidates"):
            extra["candidates"] = result["candidates"]
        return error_response(detail, **extra)
    return enc(result)
