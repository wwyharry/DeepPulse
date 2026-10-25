"""SSE 流式对话 —— 九种帧（session/round/thinking/content/tool_start/tool_result/done/error）"""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from web.api.common import error_response, parse_tool_start, sse_frame
from web.service.session_store import auto_title, is_auto_title, is_valid_session_id

logger = logging.getLogger(__name__)

router = APIRouter()

# tool_result 展示截断（agent 端已截 2000，这里再兜一层契约口径；与 TUI 2000 对齐）
TOOL_RESULT_DISPLAY_CHARS = 2000
# on_session_end 可能调 LLM，兜底超时
SESSION_END_TIMEOUT = 15.0


def _resolve_protocol(app) -> str:
    llm = (app.state.setting or {}).get("llm") or {}
    return llm.get("protocol") or "openai"


class _TurnState:
    """一轮 chat 流的收尾器：幂等 close() = 取消 consume + 落盘 + 放锁。

    断连收尾绝不押在 event_stream 生成器自身的 except/finally 上（C3/C4）：
    Starlette/uvicorn 在 http.disconnect 后取消 stream_response，取消落在
    await send()（生成器停在自身 yield）或 send 抛 OSError 时，CancelledError
    不注入生成器，生成器被遗弃直到 GC 才 aclose；连接在 body 迭代前死亡时
    生成器从未启动、finally 根本不执行。两种情况都会导致锁持续占用（同会话
    409）、consume 空跑整轮 LLM 烧 token、收尾落盘被推迟到 GC。
    故收尾统一由本对象承担，由 _SseResponse.__call__ 的 finally 强制触发。
    """

    def __init__(self, app, pool, rt, session_id: str, title: str, agent):
        self.app = app
        self.pool = pool
        self.rt = rt
        self.session_id = session_id
        self.title = title
        self.agent = agent
        self.task: asyncio.Task | None = None
        self._closed = False

    def _persist(self) -> None:
        """一轮消息 + 侧表整表落盘（title/侧表合并语义见 SessionStore.persist_turn）"""
        if self.agent is None:
            return
        agent = self.agent
        store = self.app.state.session_store
        store.persist_turn(
            self.session_id,
            title=self.title,
            protocol=getattr(getattr(agent, "client", None), "protocol", None)
            or _resolve_protocol(self.app),
            messages=list(getattr(agent, "messages", []) or []),
            user_messages=list(getattr(agent, "_user_messages", []) or []),
            session_stocks=list(getattr(agent, "_session_stocks", []) or []),
            session_topics=list(getattr(agent, "_session_topics", []) or []),
        )

    async def close(self) -> None:
        """幂等收尾：cancel consume → 等退出 → 清 running → 落盘（除非会话已删）→ 放锁。"""
        if self._closed:
            return
        self._closed = True
        try:
            if self.task is not None:
                if not self.task.done():
                    self.task.cancel()
                with contextlib.suppress(BaseException):
                    await self.task
            if self.pool.get_running_task(self.session_id) is self.task:
                self.pool.set_running_task(self.session_id, None)
            # M2 墓碑：会话已删除则绝不落盘，防 DELETE 后文件被收尾 _persist 复活
            if not getattr(self.rt, "deleted", False):
                try:
                    self._persist()
                except Exception as e:  # 落盘失败不全静默：记日志便于排查
                    logger.warning("会话 %s 落盘失败: %s", self.session_id, e)
        finally:
            if self.rt.lock.locked():
                self.rt.lock.release()


class _SseResponse(StreamingResponse):
    """SSE 响应：__call__ 的 finally 强制 aclose(body_iterator) 并执行 on_close 收尾。

    客户端断连时取消落在 await send()（生成器停在自身 yield）或 send 抛 OSError，
    生成器被遗弃到 GC 才 aclose——不能把收尾押在生成器自己的 finally 上。
    """

    def __init__(self, *args, on_close=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 先强制关闭生成器（未启动则 no-op），再显式收尾兜底
            with contextlib.suppress(BaseException):
                await self.body_iterator.aclose()
            if self._on_close is not None:
                with contextlib.suppress(BaseException):
                    await self._on_close()


@router.post("/api/chat")
async def chat(request: Request):
    """POST /api/chat → text/event-stream。忙会话直接 409，异常/取消必发 error 或 done 帧。"""
    try:
        body = await request.json()
    except Exception:
        return error_response("请求体必须是 JSON")
    if not isinstance(body, dict):
        return error_response("请求体必须是 JSON 对象")

    session_id = str(body.get("session_id") or "").strip()
    message = str(body.get("message") or "")
    if not session_id:
        return error_response("缺少 session_id")
    # m2：与 SessionStore._path 同口径白名单校验——否则整轮正常 done 但零落盘，
    # 同 id 在 GET/PATCH/DELETE 却 404，语义撕裂
    if not is_valid_session_id(session_id):
        return error_response("非法 session_id（仅允许字母、数字、下划线、连字符）")
    if not message.strip():
        return error_response("消息不能为空")

    app = request.app
    pool = app.state.agent_pool
    store = app.state.session_store

    rt = pool.runtime(session_id)
    if rt.lock.locked():
        return error_response("会话正在分析中", status_code=409)
    # locked() 与 acquire() 之间无 await，事件循环内原子生效
    await rt.lock.acquire()

    state = _TurnState(app, pool, rt, session_id, "", None)
    try:
        rt.deleted = False  # 新一轮解除删除墓碑（M2：DELETE 后同 id 再聊可重建）
        stored = store.load(session_id)
        if stored is None:
            stored = store.create(session_id=session_id)
        # 自动标题：仍为自动标题时用本条消息前 20 字刷新
        title = stored.get("title") or ""
        if is_auto_title(stored):
            title = auto_title(message)
        state.title = title
        try:
            agent = await pool.get_agent(session_id)
        except Exception as e:
            await state.close()
            return error_response(f"Agent 初始化失败: {e}", status_code=500)
        state.agent = agent
    except BaseException:
        # C4：CancelledError/GeneratorExit 不是 Exception——acquire 后任何退出路径都必须放锁
        await state.close()
        raise

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        # 事件映射：agent (type, text) → SSE 九帧
        agen = None

        async def consume():
            nonlocal agen
            try:
                agen = agent.chat_stream_async(message) if agent is not None else None
                if agen is None:
                    queue.put_nowait(("error", "Agent 不可用"))
                    return
                async for ev in agen:
                    queue.put_nowait(("ev", ev))
                queue.put_nowait(("done", None))
            except asyncio.CancelledError:
                queue.put_nowait(("cancelled", None))
                raise
            except Exception as e:
                queue.put_nowait(("error", str(e) or e.__class__.__name__))
            finally:
                if agen is not None:
                    with contextlib.suppress(Exception):
                        await agen.aclose()

        task = asyncio.create_task(consume())
        state.task = task
        pool.set_running_task(session_id, task)

        def emit(ev) -> str:
            etype, text = ev[0], ev[1]
            if etype == "round":
                try:
                    n = int(text)
                except (TypeError, ValueError):
                    n = 1
                return sse_frame("round", {"n": n})
            if etype == "thinking":
                return sse_frame("thinking", {"text": text})
            if etype == "content":
                return sse_frame("content", {"text": text})
            if etype == "tool_start":
                name, args = parse_tool_start(text)
                return sse_frame("tool_start", {"name": name, "args": args})
            if etype == "tool_result":
                t = text if isinstance(text, str) else str(text)
                return sse_frame("tool_result", {"text": t[:TOOL_RESULT_DISPLAY_CHARS]})
            if etype == "done":
                return sse_frame("done", {})
            return sse_frame("content", {"text": str(text)})

        try:
            yield sse_frame("session", {"session_id": session_id, "title": title})
            while True:
                item = await queue.get()
                kind = item[0]
                if kind == "ev":
                    ev = item[1]
                    if ev[0] == "done":
                        yield sse_frame("done", {})
                        break
                    yield emit(ev)
                elif kind == "done":
                    # 生成器自行结束但没发 done：补一帧，绝不让前端悬挂
                    yield sse_frame("done", {})
                    break
                elif kind == "cancelled":
                    yield sse_frame("error", {"message": "已停止"})
                    break
                elif kind == "error":
                    yield sse_frame("error", {"message": str(item[1])})
                    break
        except asyncio.CancelledError:
            # 生成器自身被取消（如 aclose 落在 await 上）：尽力补发 error 帧再收尾
            pool.cancel_running(session_id)
            try:
                yield sse_frame("error", {"message": "已停止"})
            except BaseException:
                pass
            raise
        except Exception as e:
            try:
                yield sse_frame("error", {"message": str(e) or "分析失败"})
            except BaseException:
                pass
        finally:
            # 常规收尾；若生成器被遗弃（C3），_SseResponse.__call__ finally 会再兜底
            await state.close()

    return _SseResponse(
        event_stream(),
        on_close=state.close,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/chat/stop")
async def chat_stop(request: Request):
    """取消会话运行中任务（无任务也 ok，幂等）"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    session_id = str((body or {}).get("session_id") or "").strip()
    if not session_id:
        return error_response("缺少 session_id")
    if not is_valid_session_id(session_id):
        return error_response("非法 session_id（仅允许字母、数字、下划线、连字符）")
    request.app.state.agent_pool.cancel_running(session_id)
    return {"status": "ok"}
