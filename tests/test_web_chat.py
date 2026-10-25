"""Web 对话 SSE 测试：全帧序列、忙会话 409、停止/取消、异常帧、落盘、自动标题。

全部离线：FakeWebAgent/HangingAgent/ErrorAgent 驱动；TestClient post 读完整流解析帧。
"""

import asyncio
import contextlib
import threading

import pytest

from tests.fakes_web import (
    ErrorAgent,
    FakeWebAgent,
    HangingAgent,
    asgi_request,
    build_raw_app,
    fake_agent_factory,
    frame_events,
    make_test_client,
    parse_sse,
    read_session_file,
    sent_sse_frames,
    sent_status,
    write_session_file,
)

JOIN_TIMEOUT = 15.0


def _post_chat_in_thread(client, payload, out: dict):
    """后台线程发 chat（SSE 流未结束前阻塞），结果写入 out"""

    def _run():
        out["resp"] = client.post("/api/chat", json=payload)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _wait_agent(cls, timeout=5.0):
    """等到假 agent 被 factory 构造出来并进入流式"""
    deadline_events = int(timeout / 0.05)
    agent = None
    for _ in range(deadline_events):
        if cls.instances:
            agent = cls.instances[-1]
            break
        threading.Event().wait(0.05)
    assert agent is not None, f"{cls.__name__} 未被构造"
    assert agent.started.wait(timeout), f"{cls.__name__} 未进入流式"
    return agent


def _new_session(client, title=None) -> str:
    body = {} if title is None else {"title": title}
    return client.post("/api/sessions", json=body).json()["session"]["id"]


@pytest.fixture
def client(tmp_path):
    with make_test_client(tmp_path) as c:
        yield c


# ── health ────────────────────────────────────────────────────────


class TestHealth:
    def test_health_shape(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["model"] == "deepseek-v4-pro"
        assert isinstance(body["agent_ready"], bool)


# ── SSE 全帧序列 ──────────────────────────────────────────────────


class TestSseFrames:
    def test_full_frame_sequence(self, client):
        sid = _new_session(client)
        r = client.post("/api/chat", json={"session_id": sid, "message": "帮我看看贵州茅台"})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        frames = parse_sse(r.text)
        # 严格顺序：session → round → thinking → content → tool_start → tool_result → done
        assert frame_events(frames) == [
            "session",
            "round",
            "thinking",
            "content",
            "tool_start",
            "tool_result",
            "done",
        ]
        by_type = {f["event"]: f["data"] for f in frames}

        assert by_type["session"]["session_id"] == sid
        assert isinstance(by_type["session"]["title"], str)
        # round 的 n 是 int（agent 侧 text 本就是 int）
        assert by_type["round"]["n"] == 1
        assert isinstance(by_type["round"]["n"], int)
        assert set(by_type["thinking"]) == {"text"}
        assert "基本面" in by_type["thinking"]["text"]
        assert set(by_type["content"]) == {"text"}
        assert "趋势" in by_type["content"]["text"]
        # tool_start：name + args 对象（工具执行完才到达）
        assert by_type["tool_start"] == {"name": "get_kline", "args": {"code": "600519"}}
        assert isinstance(by_type["tool_start"]["args"], dict)
        assert set(by_type["tool_result"]) == {"text"}
        assert "price" in by_type["tool_result"]["text"]
        assert by_type["done"] == {}

    def test_content_tail_is_not_the_end_signal(self, tmp_path):
        """max_rounds 耗尽路径先发 content 提示再 done——以 done 收束"""
        factory = fake_agent_factory(events=[("round", 3), ("content", "已达最大轮次，先给出当前结论。"), ("done", "")])
        with make_test_client(tmp_path, agent_factory=factory) as client:
            sid = _new_session(client)
            r = client.post("/api/chat", json={"session_id": sid, "message": "多轮问题"})
            assert r.status_code == 200
            frames = parse_sse(r.text)
            assert frame_events(frames)[-1] == "done"
            assert frames[-2]["event"] == "content"
            assert frames[-1]["data"] == {}

    def test_error_agent_emits_error_frame_without_done(self, tmp_path):
        with make_test_client(tmp_path, agent_factory=ErrorAgent) as client:
            sid = _new_session(client)
            r = client.post("/api/chat", json={"session_id": sid, "message": "这轮会炸"})
            assert r.status_code == 200
            frames = parse_sse(r.text)
            events = frame_events(frames)
            assert "error" in events, "异常必须发 error 帧，绝不让前端悬挂"
            assert events[-1] == "error"
            assert "done" not in events, "异常路径不发 done"
            err = frames[-1]["data"]
            assert set(err) == {"message"}
            assert isinstance(err["message"], str) and err["message"].strip()


# ── 并发忙 / 停止 ─────────────────────────────────────────────────


class TestBusyAndStop:
    def test_concurrent_same_session_gets_409(self, tmp_path):
        with make_test_client(tmp_path, agent_factory=HangingAgent) as client:
            sid = _new_session(client)
            out = {}
            t = _post_chat_in_thread(client, {"session_id": sid, "message": "长分析"}, out)
            _wait_agent(HangingAgent)
            # 同会话第二路：锁被占 → 发流前 409
            r2 = client.post("/api/chat", json={"session_id": sid, "message": "再来一问"})
            assert r2.status_code == 409
            assert r2.json()["detail"] == "会话正在分析中"
            # 收尾：停止第一路，避免悬挂线程
            assert client.post("/api/chat/stop", json={"session_id": sid}).json() == {"status": "ok"}
            t.join(JOIN_TIMEOUT)
            assert not t.is_alive()

    def test_different_sessions_can_run_concurrently(self, tmp_path):
        with make_test_client(tmp_path, agent_factory=HangingAgent) as client:
            sid1, sid2 = _new_session(client), _new_session(client)
            out1, out2 = {}, {}
            t1 = _post_chat_in_thread(client, {"session_id": sid1, "message": "分析A"}, out1)
            t2 = _post_chat_in_thread(client, {"session_id": sid2, "message": "分析B"}, out2)
            started = []
            for _ in range(100):
                started = [a for a in HangingAgent.instances if a.started.is_set()]
                if len(started) >= 2:
                    break
                threading.Event().wait(0.05)
            assert len(started) == 2, "不同会话应可并发，不应被全局锁互斥"
            for sid in (sid1, sid2):
                client.post("/api/chat/stop", json={"session_id": sid})
            t1.join(JOIN_TIMEOUT)
            t2.join(JOIN_TIMEOUT)
            assert not t1.is_alive() and not t2.is_alive()

    def test_stop_idempotent_without_active_task(self, client):
        sid = _new_session(client)
        for _ in range(2):
            r = client.post("/api/chat/stop", json={"session_id": sid})
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}

    def test_stop_cancels_hanging_stream_with_stopped_frame(self, tmp_path):
        with make_test_client(tmp_path, agent_factory=HangingAgent) as client:
            sid = _new_session(client)
            out = {}
            t = _post_chat_in_thread(client, {"session_id": sid, "message": "长分析"}, out)
            agent = _wait_agent(HangingAgent)
            r = client.post("/api/chat/stop", json={"session_id": sid})
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}
            t.join(JOIN_TIMEOUT)
            assert not t.is_alive(), "停止后 SSE 流必须收尾，不得悬挂"
            assert agent.cancelled, "HangingAgent 应被 cancel"
            frames = parse_sse(out["resp"].text)
            events = frame_events(frames)
            assert events[-1] == "error", f"取消必须以 error 帧收尾，实际 {events}"
            assert frames[-1]["data"] == {"message": "已停止"}
            assert "done" not in events


# ── 落盘 / 自动标题 ───────────────────────────────────────────────


class TestChatPersistenceAndTitle:
    def test_done_persists_messages(self, tmp_path):
        with make_test_client(tmp_path) as client:
            sid = _new_session(client)
            r = client.post("/api/chat", json={"session_id": sid, "message": "给个简评"})
            assert r.status_code == 200
            assert "done" in frame_events(parse_sse(r.text))
            data = read_session_file(tmp_path / "sessions", sid)
            assert data["messages"], "done 后必须立即持久化"
            assert data["user_messages"] == ["给个简评"]
            roles = [m.get("role") for m in data["messages"]]
            assert "user" in roles and "assistant" in roles
            # FakeWebAgent 的 openai 形 tool_calls 原样落库，arguments 是字符串
            tool_calls = [tc for m in data["messages"] if m.get("tool_calls") for tc in m["tool_calls"]]
            assert tool_calls, "落库应保留 tool_calls"
            assert isinstance(tool_calls[0]["function"]["arguments"], str)

    def test_auto_title_first_20_chars_and_refresh(self, tmp_path):
        with make_test_client(tmp_path) as client:
            sid = _new_session(client)
            msg1 = "一二三四五六七八九十一二三四五六七八九十个字的问题"
            r = client.post("/api/chat", json={"session_id": sid, "message": msg1})
            assert r.status_code == 200
            frames = parse_sse(r.text)
            assert frames[0]["event"] == "session"
            title1 = msg1[:20]
            assert frames[0]["data"]["title"] == title1, "首发 session 帧携带自动标题"
            sessions = client.get("/api/sessions").json()["sessions"]
            assert [s for s in sessions if s["id"] == sid][0]["title"] == title1

            # 标题仍是自动标题 → 新一轮用新消息前 20 字刷新
            msg2 = "第二问" + "甲" * 20
            r2 = client.post("/api/chat", json={"session_id": sid, "message": msg2})
            assert r2.status_code == 200
            title2 = msg2[:20]
            assert parse_sse(r2.text)[0]["data"]["title"] == title2

            # 手工改名后不再被自动覆盖
            assert client.patch(f"/api/sessions/{sid}", json={"title": "手工标题"}).status_code == 200
            r3 = client.post("/api/chat", json={"session_id": sid, "message": "第三问不该改标题"})
            assert r3.status_code == 200
            assert parse_sse(r3.text)[0]["data"]["title"] == "手工标题"


# ── 假 agent 接口契约（防实现改签名后测试空转） ───────────────────


class TestFakeAgentContract:
    def test_factory_builds_agent_with_session_id(self):
        factory = fake_agent_factory(events=[("done", "")])
        agent = factory("sid-123")
        assert isinstance(agent, FakeWebAgent)
        assert agent.session_id == "sid-123"
        assert callable(agent.on_session_end) and callable(agent.reset)
        assert isinstance(agent.messages, list) and agent.messages[0]["role"] == "system"
        # protocol 闸门可测：假 agent 必须带 client.protocol（M10 回灌协议比对）
        assert getattr(getattr(agent, "client", None), "protocol", None)


# ── 断连/取消收尾（C3/C4）────────────────────────────────────────


class TestDisconnectCleanup:
    """C3/C4：客户端断连或 setup 期取消后锁必须立即释放，绝不等 GC，无第二路 409。"""

    def test_send_error_mid_stream_releases_lock(self, tmp_path):
        """C3：send 中途抛 OSError（客户端断连）→ 生成器被 aclose、锁立即放、consume 被取消"""

        async def run():
            app = build_raw_app(tmp_path, agent_factory=HangingAgent)
            async with app.router.lifespan_context(app):
                pool = app.state.agent_pool
                sid = app.state.session_store.create(None, "")["id"]
                body_chunks = {"n": 0}

                def fail_on_second_body(message):
                    if message.get("type") == "http.response.body" and message.get("body"):
                        body_chunks["n"] += 1
                        if body_chunks["n"] >= 2:
                            raise OSError("客户端断连")

                await asgi_request(
                    app,
                    "POST",
                    "/api/chat",
                    {"session_id": sid, "message": "长分析"},
                    send_hook=fail_on_second_body,
                )
                rt = pool.runtime(sid)
                assert not rt.lock.locked(), "断连收尾必须立即放锁，不能等 GC"
                agent = HangingAgent.instances[-1]
                assert agent.started.is_set(), "用例前提：consume 已在跑（thinking 帧已发出）"
                assert agent.cancelled, "consume 必须被取消，不得空跑整轮 LLM"
                assert rt.task is None or rt.task.done(), "running task 必须清掉"
                # 第二路同会话必须 200 + session 帧（不是 409）
                sent2, _ = await asgi_request(
                    app,
                    "POST",
                    "/api/chat",
                    {"session_id": sid, "message": "再来一问"},
                    send_hook=fail_on_second_body,
                )
                assert sent_status(sent2) == 200, "第二路不得 409"
                frames2 = sent_sse_frames(sent2)
                assert frames2 and frames2[0]["event"] == "session"

        asyncio.run(run())

    def test_generator_never_started_still_releases_lock(self, tmp_path):
        """C4b：body 迭代前连接死亡（首包 send 即失败）→ 生成器从未启动、finally 不执行，也必须放锁"""

        async def run():
            app = build_raw_app(tmp_path, agent_factory=FakeWebAgent)
            async with app.router.lifespan_context(app):
                pool = app.state.agent_pool
                sid = app.state.session_store.create(None, "")["id"]

                def fail_immediately(_message):
                    raise OSError("body 迭代前断连")

                await asgi_request(
                    app,
                    "POST",
                    "/api/chat",
                    {"session_id": sid, "message": "问"},
                    send_hook=fail_immediately,
                )
                rt = pool.runtime(sid)
                assert not rt.lock.locked(), "生成器未启动时 finally 不会执行，也必须放锁"
                assert rt.task is None, "consume 从未启动"
                sent2, _ = await asgi_request(app, "POST", "/api/chat", {"session_id": sid, "message": "再问"})
                assert sent_status(sent2) == 200, "第二路不得 409"
                assert "done" in frame_events(sent_sse_frames(sent2))

        asyncio.run(run())

    def test_cancel_mid_stream_releases_lock(self, tmp_path):
        """C3 取消路径：流式中取消外层任务 → 锁释放、agent 被取消、第二路不 409"""

        async def run():
            app = build_raw_app(tmp_path, agent_factory=HangingAgent)
            async with app.router.lifespan_context(app):
                pool = app.state.agent_pool
                sid = app.state.session_store.create(None, "")["id"]
                task = asyncio.create_task(
                    asgi_request(app, "POST", "/api/chat", {"session_id": sid, "message": "长分析"})
                )
                for _ in range(400):
                    if HangingAgent.instances and HangingAgent.instances[-1].started.is_set():
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("HangingAgent 未进入流式")
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                rt = pool.runtime(sid)
                for _ in range(200):
                    if not rt.lock.locked():
                        break
                    await asyncio.sleep(0.01)
                assert not rt.lock.locked(), "流式中取消也必须放锁"
                assert HangingAgent.instances[-1].cancelled, "consume 必须随收尾被取消"

                def fail_on_body(message):
                    if message.get("type") == "http.response.body" and message.get("body"):
                        raise OSError("再次断连")

                sent2, _ = await asgi_request(
                    app,
                    "POST",
                    "/api/chat",
                    {"session_id": sid, "message": "再来一问"},
                    send_hook=fail_on_body,
                )
                assert sent_status(sent2) == 200, "第二路不得 409"

        asyncio.run(run())

    def test_cancelled_during_get_agent_releases_lock(self, tmp_path):
        """C4a：acquire 后 get_agent 期间被取消（CancelledError 不是 Exception）→ 必须放锁"""

        async def run():
            app = build_raw_app(tmp_path, agent_factory=FakeWebAgent)
            async with app.router.lifespan_context(app):
                pool = app.state.agent_pool
                sid = app.state.session_store.create(None, "")["id"]
                started = asyncio.Event()

                async def hanging_get_agent(_sid):
                    started.set()
                    await asyncio.Event().wait()

                pool.get_agent = hanging_get_agent
                task = asyncio.create_task(asgi_request(app, "POST", "/api/chat", {"session_id": sid, "message": "问"}))
                await asyncio.wait_for(started.wait(), timeout=5)
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                rt = pool.runtime(sid)
                assert not rt.lock.locked(), "CancelledError 不是 Exception：acquire 后任何退出路径都必须放锁"

        asyncio.run(run())


# ── 非法 session_id（m2）─────────────────────────────────────────


class TestIllegalSessionId:
    def test_illegal_session_id_rejected(self, client):
        """m2：非法 id 一律 400 中文 detail，不许整轮 done 但零落盘"""
        for bad in ("bad/id", "../evil", "bad!id", "a b"):
            r = client.post("/api/chat", json={"session_id": bad, "message": "问"})
            assert r.status_code == 400, f"非法 id {bad!r} 必须 400"
            assert "session_id" in r.json().get("detail", "")
        r = client.post("/api/chat/stop", json={"session_id": "bad/id"})
        assert r.status_code == 400
        assert "session_id" in r.json().get("detail", "")

    def test_legal_session_id_persists_normally(self, client, tmp_path):
        """m2：合法 id（含连字符/下划线）正常落盘，不被误伤"""
        sid = "ok-id_1"
        r = client.post("/api/chat", json={"session_id": sid, "message": "合法问题"})
        assert r.status_code == 200
        assert "done" in frame_events(parse_sse(r.text))
        data = read_session_file(tmp_path / "sessions", sid)
        assert data["id"] == sid
        assert data["user_messages"] == ["合法问题"]


# ── 自动标题空白口径（m12）───────────────────────────────────────


class TestAutoTitleWhitespace:
    def test_auto_title_refresh_with_whitespace_message(self, client):
        """m12：首条消息带前后空白 → 候选须 strip 后截 20，自动标题刷新不得永久失效"""
        sid = _new_session(client)
        msg1 = "  首问带前后空白  \n"
        r1 = client.post("/api/chat", json={"session_id": sid, "message": msg1})
        assert r1.status_code == 200
        title1 = msg1.strip()[:20]
        assert parse_sse(r1.text)[0]["data"]["title"] == title1
        msg2 = "第二问刷新标题"
        r2 = client.post("/api/chat", json={"session_id": sid, "message": msg2})
        assert r2.status_code == 200
        assert parse_sse(r2.text)[0]["data"]["title"] == msg2.strip()[:20], (
            "空白消息不得让自动标题刷新永久失效（is_auto_title 候选未 strip）"
        )


# ── 分析中手工改名不被收尾覆盖（m1）─────────────────────────────


class TestRenameDuringAnalysis:
    def test_manual_rename_survives_turn_persist(self, tmp_path):
        """m1：首轮长分析中 PATCH 改名 → 收尾 _persist 不得用标题快照冲掉手工标题"""

        with make_test_client(tmp_path, agent_factory=HangingAgent) as client:
            sid = _new_session(client)
            out: dict = {}
            t = _post_chat_in_thread(client, {"session_id": sid, "message": "问" * 30}, out)
            _wait_agent(HangingAgent)
            assert client.patch(f"/api/sessions/{sid}", json={"title": "手工标题"}).status_code == 200
            assert client.post("/api/chat/stop", json={"session_id": sid}).status_code == 200
            t.join(JOIN_TIMEOUT)
            assert not t.is_alive()
            data = read_session_file(tmp_path / "sessions", sid)
            assert data["title"] == "手工标题", "收尾 _persist 用标题快照冲掉了手工改名"
            assert data["user_messages"] == ["问" * 30], "消息照常落盘"

    def test_manual_rename_survives_turn_persist_with_history(self, tmp_path):
        """m1：已有自动标题历史的会话，分析中改名同样不被快照冲掉"""

        with make_test_client(tmp_path, agent_factory=HangingAgent) as client:
            root = tmp_path / "sessions"
            sid = "rename-mid-hist"
            write_session_file(
                root,
                sid,
                messages=[{"role": "user", "content": "老问题"}],
                user_messages=["老问题"],
                title="老问题",  # 自动标题形态
            )
            out: dict = {}
            t = _post_chat_in_thread(client, {"session_id": sid, "message": "新问题续跑"}, out)
            _wait_agent(HangingAgent)
            assert client.patch(f"/api/sessions/{sid}", json={"title": "手工标题"}).status_code == 200
            assert client.post("/api/chat/stop", json={"session_id": sid}).status_code == 200
            t.join(JOIN_TIMEOUT)
            assert not t.is_alive()
            data = read_session_file(root, sid)
            assert data["title"] == "手工标题"
            assert data["user_messages"] == ["老问题", "新问题续跑"]
