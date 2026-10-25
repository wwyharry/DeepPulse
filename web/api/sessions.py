"""历史会话 CRUD —— 创建不建 StockAgent，删除后台 on_session_end + 删文件"""

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from web.api.common import error_response
from web.service.session_store import is_valid_session_id

router = APIRouter()

SESSION_END_TIMEOUT = 15.0
# 后台 on_session_end 任务引用（防 GC 提前回收）
_bg_tasks: set = set()

_BAD_SID_DETAIL = "非法 session_id（仅允许字母、数字、下划线、连字符）"


class SessionCreateIn(BaseModel):
    title: str | None = None


class SessionPatchIn(BaseModel):
    title: str


@router.get("/api/sessions")
async def list_sessions(request: Request):
    """会话列表（updated_at 倒序）"""
    sessions = await asyncio.to_thread(request.app.state.session_store.list)
    return {"sessions": sessions}


@router.post("/api/sessions")
async def create_session(request: Request, body: SessionCreateIn | None = None):
    """新建空会话（不建 StockAgent，避免开标签页就联网 5s）"""
    title = (body.title if body else None) or ""
    data = await asyncio.to_thread(request.app.state.session_store.create, None, title)
    return {"session": request.app.state.session_store.summary(data)}


@router.patch("/api/sessions/{session_id}")
async def rename_session(request: Request, session_id: str, body: SessionPatchIn):
    """改名（title 为必填）"""
    if not is_valid_session_id(session_id):
        return error_response(_BAD_SID_DETAIL)
    store = request.app.state.session_store
    data = await asyncio.to_thread(store.rename, session_id, body.title)
    if data is None:
        return error_response("会话不存在", status_code=404)
    return {"session": store.summary(data)}


@router.delete("/api/sessions/{session_id}")
async def delete_session(request: Request, session_id: str):
    """删除会话：取消在途 turn + 立墓碑防文件复活 + 后台持锁 on_session_end + 删文件"""
    if not is_valid_session_id(session_id):
        return error_response(_BAD_SID_DETAIL)
    app = request.app
    store = app.state.session_store
    pool = app.state.agent_pool

    stored = await asyncio.to_thread(store.load, session_id)
    if stored is None:
        return error_response("会话不存在", status_code=404)

    rt = pool.runtime(session_id)
    agent = rt.agent
    # M2：先立删除墓碑再取消在途 turn——收尾 _persist 见墓碑即跳过，
    # 否则 DELETE 返回 ok 后文件被 chat 的 _persist 整表写回复活回列表
    rt.deleted = True
    pool.cancel_running(session_id)
    rt.agent = None

    async def _end_agent():
        """记忆收尾：持锁执行，保证不与在途 turn 的 chat_stream_async 并发踩同一 agent 实例"""
        if agent is None:
            return
        try:
            # 与在途 turn 协同：等它收尾放锁后再做 on_session_end（超时兜底）
            await asyncio.wait_for(rt.lock.acquire(), timeout=SESSION_END_TIMEOUT)
        except Exception:
            return
        try:
            # forget=True：被删会话不沉淀摘要/画像（删除意图不得被记忆系统反向抵消）
            await asyncio.wait_for(
                asyncio.to_thread(agent.on_session_end, True), timeout=SESSION_END_TIMEOUT
            )
        except Exception:
            pass
        finally:
            rt.lock.release()

    # 后台收尾 + 立即删文件（列表/404 语义即时生效）
    bg = asyncio.create_task(_end_agent())
    _bg_tasks.add(bg)
    bg.add_done_callback(_bg_tasks.discard)
    ok = await asyncio.to_thread(store.delete, session_id)
    if not ok:
        return error_response("会话不存在", status_code=404)
    return {"status": "ok"}


@router.get("/api/sessions/{session_id}/messages")
async def session_messages(request: Request, session_id: str):
    """回放消息（stored 形状：arguments 保持 JSON 字符串不再解码）"""
    if not is_valid_session_id(session_id):
        return error_response(_BAD_SID_DETAIL)
    stored = await asyncio.to_thread(request.app.state.session_store.load, session_id)
    if stored is None:
        return error_response("会话不存在", status_code=404)
    return {
        "session_id": stored.get("id") or session_id,
        "title": stored.get("title") or "",
        "messages": list(stored.get("messages") or []),
    }
