"""系统管理 REST API"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class MessageCreate(BaseModel):
    role: str
    content: str


class TitleUpdate(BaseModel):
    title: str


@router.get("/status")
async def get_status():
    """系统状态"""
    from web.app.services.agent_pool import agent_pool
    from web.app.services.session_store import session_store

    return {
        "status": "running",
        "active_sessions": len(agent_pool.list_sessions()),
        "saved_sessions": len(session_store.list_sessions()),
    }


@router.get("/sessions")
async def list_sessions():
    """会话列表"""
    from web.app.services.session_store import session_store

    return {"sessions": session_store.list_sessions()}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """会话详情"""
    from web.app.services.session_store import session_store

    messages = session_store.get_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@router.post("/sessions/{session_id}/messages")
async def add_session_message(session_id: str, body: MessageCreate):
    """向会话添加消息"""
    from web.app.services.session_store import session_store

    session_store.save_message(session_id, {
        "role": body.role,
        "content": body.content,
    })
    return {"status": "saved"}


@router.post("/sessions/{session_id}/title")
async def update_session_title(session_id: str, body: TitleUpdate):
    """更新会话标题"""
    from web.app.services.session_store import session_store

    session_store.save_title(session_id, body.title)
    return {"status": "saved", "title": body.title}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    from web.app.services.agent_pool import agent_pool
    from web.app.services.session_store import session_store

    agent_pool.remove(session_id)
    session_store.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}
