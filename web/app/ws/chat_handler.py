"""WebSocket 对话处理 — 流式输出 Agent 分析结果（含推理过程）"""

import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.app.services.agent_pool import agent_pool
from web.app.services.session_store import session_store

router = APIRouter()


@router.websocket("/chat")
async def chat_ws(websocket: WebSocket):
    """WebSocket 对话端点

    协议：
    Client → Server:
        {"type": "message", "content": "分析贵州茅台", "session_id": "xxx"}
        {"type": "stop"}
        {"type": "judge", "session_id": "xxx"}

    Server → Client:
        {"type": "session", "session_id": "abc123"}
        {"type": "thinking", "delta": "..."}
        {"type": "content", "delta": "..."}
        {"type": "tool_call", "name": "...", "args": {...}}
        {"type": "tool_result", "name": "...", "data": "..."}
        {"type": "judge_start"}
        {"type": "judge_content", "delta": "..."}
        {"type": "done", "rounds": N, "tools": M}
        {"type": "error", "message": "..."}
    """
    await websocket.accept()

    session_id = None
    cancelled = False

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            if data["type"] == "stop":
                cancelled = True
                continue

            if data["type"] == "judge":
                # 评判 Agent
                sid = data.get("session_id") or session_id
                if not sid:
                    await websocket.send_json({"type": "error", "message": "请先进行一次对话再评测"})
                    continue

                agent = agent_pool.get_or_create(sid)
                await _run_judge(websocket, agent)
                continue

            if data["type"] == "message":
                cancelled = False
                content = data["content"]
                session_id = data.get("session_id") or str(uuid.uuid4())

                # 发送会话 ID
                await websocket.send_json({"type": "session", "session_id": session_id})

                # 获取 Agent（会自动加载历史消息）
                agent = agent_pool.get_or_create(session_id)

                # 流式输出
                full_content = ""
                try:
                    async for event in agent.chat_stream_json(content):
                        if cancelled:
                            break

                        await websocket.send_json(event)

                        # 收集完整内容用于保存
                        if event["type"] == "content":
                            full_content += event.get("delta", "")

                    # 保存用户消息和助手回复到会话存储
                    session_store.save_message(session_id, {"role": "user", "content": content})
                    if full_content:
                        session_store.save_message(session_id, {
                            "role": "assistant",
                            "content": full_content,
                        })

                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e),
                    })

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "message": "无效的 JSON 格式"})
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


async def _run_judge(websocket: WebSocket, agent):
    """运行评判 Agent（快速评测）"""
    from deeppulse.agent.judge_agent import JudgeAgent, JudgeHistory

    await websocket.send_json({"type": "judge_start"})

    try:
        judge = JudgeAgent(setting=agent.setting)
        full_content = ""
        summary = {}

        async for event in judge.judge_stream_async(agent.messages):
            if isinstance(event, tuple):
                event_type, content = event
            else:
                event_type = getattr(event, 'type', None)
                content = getattr(event, 'text', '')

            if event_type == "content":
                full_content += content
                await websocket.send_json({
                    "type": "judge_content",
                    "delta": content,
                })

            elif event_type == "score":
                await websocket.send_json({
                    "type": "judge_score",
                    "score": content,
                })

            elif event_type == "dimension_score":
                await websocket.send_json({
                    "type": "judge_dimension",
                    "data": json.loads(content) if isinstance(content, str) else content,
                })

            elif event_type == "summary":
                summary = json.loads(content) if isinstance(content, str) else content
                await websocket.send_json({
                    "type": "judge_summary",
                    "data": summary,
                })

        # 保存评测历史
        history = JudgeHistory()
        history.save_evaluation(agent.session_id, summary, full_content)

        await websocket.send_json({"type": "judge_done"})

    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": f"评测失败: {str(e)}",
        })
