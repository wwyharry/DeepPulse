"""市场面板 —— 指数实时（新浪直连）与市场概览（涨跌家数统计）"""

import asyncio
import json

from fastapi import APIRouter, Request

from web.api.common import enc, error_response

router = APIRouter()


@router.get("/api/market/indices")
async def market_indices(request: Request):
    """三大指数实时。失败时 error 填原因、indices=[]（前端显示占位）。"""
    from web.service.indices import fetch_indices

    indices, err = await asyncio.to_thread(fetch_indices)
    return {"indices": enc(indices), "error": err}


@router.get("/api/market/overview")
async def market_overview(request: Request):
    """市场概览（涨跌家数/分布统计，非指数行情）。market_overview() 返回 JSON 字符串须 loads。"""
    from deeppulse.agent.tools.market import market_overview as _market_overview

    try:
        raw = await asyncio.to_thread(_market_overview)
        data = json.loads(raw)
    except Exception as e:
        return error_response(f"市场概览获取失败: {e}", status_code=502)
    return {"overview": enc(data)}
