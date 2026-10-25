"""自选股面板 —— list/add/remove，行情缺码 price 为 null（前端显示 '--'）"""

import asyncio

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from web.api.common import enc, error_response

router = APIRouter()


class WatchlistAddIn(BaseModel):
    code: str
    group: str | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    notes: str | None = None


def _num(v):
    """数值或 None：pandas NULL→NaN / 非有限值一律 None（契约「数值或 null」）"""
    import math

    if isinstance(v, bool):
        return v
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _item_view(row: dict, quotes: dict) -> dict:
    """自选记录 + 实时行情 → 面板行（价格数值或 null；volume 保持股）"""
    code = str(row.get("code") or "")
    q = quotes.get(code)
    item = {
        "code": code,
        "name": (q.name if q and q.name else None) or row.get("name") or code,
        "group": row.get("group_name") or "默认",
        "price": _num(q.current if q else None),
        "change_pct": _num(q.change_pct if q else None),
        "target_price": _num(row.get("target_price")),
        "stop_loss": _num(row.get("stop_loss_price")),
        "notes": row.get("notes") or "",
    }
    if q is not None and getattr(q, "volume", None) is not None:
        # volume 单位保持「股」（tools 层展示才转手）
        item["volume"] = _num(q.volume)
    return item


@router.get("/api/watchlist")
async def watchlist_get(request: Request):
    """自选股列表（含现价/涨跌幅；缺码为 null）"""
    s = request.app.state.singletons

    def _load():
        rows = s.watchlist.list()
        codes = [str(r.get("code") or "") for r in rows]
        quotes = s.fetch_quotes(codes)
        return rows, quotes

    rows, quotes = await asyncio.to_thread(_load)
    items = [_item_view(r, quotes) for r in rows]
    return {"items": enc(items)}


@router.post("/api/watchlist")
async def watchlist_add(request: Request, body: WatchlistAddIn):
    """添加自选股；无法识别/歧义 → 400 + detail + candidates"""
    s = request.app.state.singletons
    result = await asyncio.to_thread(
        s.watchlist.add,
        body.code,
        "",
        body.group or "默认",
        body.target_price,
        body.stop_loss,
        body.notes or "",
    )
    if not isinstance(result, dict) or result.get("status") != "ok":
        detail = (result or {}).get("message") or "添加失败"
        extra = {}
        if (result or {}).get("candidates"):
            extra["candidates"] = result["candidates"]
        return error_response(detail, **extra)
    return {
        "status": "ok",
        "code": result.get("code"),
        "name": result.get("name"),
        "group": result.get("group"),
    }


@router.delete("/api/watchlist/{code}")
async def watchlist_delete(request: Request, code: str, group: str = Query("默认")):
    """移除自选股；没删掉 → 400 + detail（照搬 manager 中文 message；分组不符附 groups）"""
    s = request.app.state.singletons
    result = await asyncio.to_thread(s.watchlist.remove, code, group)
    if not isinstance(result, dict) or result.get("status") != "ok":
        detail = (result or {}).get("message") or "删除失败"
        extra = {}
        if (result or {}).get("groups"):
            extra["groups"] = result["groups"]
        return error_response(detail, **extra)
    return {
        "status": "ok",
        "code": result.get("code"),
        "name": result.get("name"),
        "group": result.get("group"),
        "removed": result.get("removed", 1),
    }
