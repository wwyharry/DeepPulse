"""主页看板 API —— 全部面板一次取回（前端 60s 轮询）

看板铁律：仅数据整理，响应体绝无任何算法推荐/评分/操作建议字段（禁词测试钉死）。
面板级错误内嵌 {data,error,source,stale}，整体恒 200。
"""

import asyncio

from fastapi import APIRouter, Request

from web.api.common import enc

router = APIRouter()


@router.get("/api/dashboard")
async def dashboard(request: Request, refresh: int = 0):
    """全部看板面板。refresh=1 跳过 TTL 缓存（手动刷新按钮用）。GET 不过 SameOriginGuard。"""
    from web.service.dashboard_service import fetch_dashboard

    payload = await asyncio.to_thread(fetch_dashboard, bool(refresh))
    return enc(payload)
