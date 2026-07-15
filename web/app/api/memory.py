"""记忆系统 REST API"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class MemorySearch(BaseModel):
    query: str
    limit: int = 10


class MemoryCreate(BaseModel):
    content: str
    memory_type: str = "insight"


# ── 记忆 ────────────────────────────────────────────


@router.get("/memories")
async def list_memories(limit: int = Query(20, ge=1, le=100)):
    """记忆列表"""
    from web.app.services.agent_pool import agent_pool

    # 使用默认 Agent 获取记忆管理器
    agent = agent_pool.get_or_create("_web_default")
    result = agent.memory.list_memories(limit=limit)
    return {"memories": result}


@router.post("/memories/search")
async def search_memories(body: MemorySearch):
    """搜索记忆"""
    from web.app.services.agent_pool import agent_pool

    agent = agent_pool.get_or_create("_web_default")
    result = agent.memory.search_memories(body.query, top_k=body.limit)
    return {"memories": result}


@router.post("/memories")
async def create_memory(body: MemoryCreate):
    """保存记忆"""
    from web.app.services.agent_pool import agent_pool

    agent = agent_pool.get_or_create("_web_default")
    result = agent.memory.save_memory(body.content, memory_type=body.memory_type)
    return {"memory": result}


# ── 预测 ────────────────────────────────────────────


@router.get("/predictions")
async def list_predictions():
    """预测记录"""
    from web.app.services.agent_pool import agent_pool

    agent = agent_pool.get_or_create("_web_default")
    stats = agent.memory.prediction_tracker.get_accuracy_stats()
    return {"predictions": stats}


# ── 用户画像 ────────────────────────────────────────


@router.get("/profile")
async def get_profile():
    """用户画像"""
    from web.app.services.agent_pool import agent_pool

    agent = agent_pool.get_or_create("_web_default")
    result = agent.memory.user_profile.get_profile()
    return {"profile": result}


# ── 知识图谱 ────────────────────────────────────────


@router.get("/knowledge")
async def get_knowledge():
    """知识图谱"""
    from web.app.services.agent_pool import agent_pool

    agent = agent_pool.get_or_create("_web_default")
    result = agent.memory.knowledge_graph.query_related()
    return {"knowledge": result}
