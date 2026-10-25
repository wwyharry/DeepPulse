"""Web 会话 API 测试：列表排序、改名/删除、回灌形状、坏 JSON 容错、cap 200、落盘内容。

全部离线：session_root 注入 tmp 目录，绝不落真实 ~/.deeppulse。
"""

import json
import threading

import pytest

from tests.fakes_web import (
    NEW_SYSTEM_TEXT,
    STALE_SYSTEM_TEXT,
    FakeWebAgent,
    HangingAgent,
    make_test_client,
    read_session_file,
    stored_history_messages,
    write_session_file,
)


@pytest.fixture
def client(tmp_path):
    with make_test_client(tmp_path) as c:
        yield c


@pytest.fixture
def session_root(tmp_path):
    return tmp_path / "sessions"


# ── 创建 / 列表 / 改名 / 删除 / 404 ────────────────────────────────


class TestSessionCrud:
    def test_create_does_not_build_agent(self, client):
        r = client.post("/api/sessions", json={"title": "盘面复盘"})
        assert r.status_code == 200
        session = r.json()["session"]
        assert session["title"] == "盘面复盘"
        assert session["id"]
        assert session["message_count"] == 0
        assert "created_at" in session and "updated_at" in session
        # 空会话不建 StockAgent（避免开标签页就联网 5s）
        assert FakeWebAgent.instances == []

    def test_list_sorted_by_updated_at_desc(self, client, session_root):
        write_session_file(session_root, "s-a", title="最早", updated_at="2026-01-01T00:00:01",
                           created_at="2026-01-01T00:00:01")
        write_session_file(session_root, "s-b", title="居中", updated_at="2026-01-02T00:00:01",
                           created_at="2026-01-01T00:00:02")
        write_session_file(session_root, "s-c", title="最新", updated_at="2026-01-03T00:00:01",
                           created_at="2026-01-01T00:00:03")
        r = client.get("/api/sessions")
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert [s["id"] for s in sessions] == ["s-c", "s-b", "s-a"]
        for s in sessions:
            assert set(s) >= {"id", "title", "created_at", "updated_at", "message_count"}
            assert isinstance(s["message_count"], int)

    def test_patch_rename(self, client):
        sid = client.post("/api/sessions", json={}).json()["session"]["id"]
        r = client.patch(f"/api/sessions/{sid}", json={"title": "改名后的会话"})
        assert r.status_code == 200
        assert r.json()["session"]["title"] == "改名后的会话"
        sessions = client.get("/api/sessions").json()["sessions"]
        assert [s for s in sessions if s["id"] == sid][0]["title"] == "改名后的会话"

    def test_delete_removes_file(self, client, session_root):
        sid = client.post("/api/sessions", json={}).json()["session"]["id"]
        r = client.delete(f"/api/sessions/{sid}")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        assert not (session_root / f"{sid}.json").exists()
        assert all(s["id"] != sid for s in client.get("/api/sessions").json()["sessions"])

    def test_404_missing_session(self, client):
        assert client.get("/api/sessions/no-such-id/messages").status_code == 404
        detail = client.get("/api/sessions/no-such-id/messages").json().get("detail", "")
        assert isinstance(detail, str) and detail.strip()
        r = client.patch("/api/sessions/no-such-id", json={"title": "x"})
        assert r.status_code == 404
        # DELETE 缺失会话：契约未钉死 200/404，但必须不 5xx 且不静默报 ok 别人的会话
        r = client.delete("/api/sessions/no-such-id")
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert r.json().get("status") == "ok"


# ── 消息回灌 ──────────────────────────────────────────────────────


class TestMessagesReplay:
    def test_replay_new_system_plus_stored_rest(self, client, session_root, tmp_path):
        """回灌 = [新 system] + stored[1:]；tool_calls.arguments 保持字符串不二次解码"""
        stored = stored_history_messages()
        write_session_file(session_root, "s-replay", messages=stored, title="回灌用例")
        # 走一轮对话触发建 agent + 回灌（factory 产出新 system）
        r = client.post("/api/chat", json={"session_id": "s-replay", "message": "新问题：给个结论"})
        assert r.status_code == 200
        agent = FakeWebAgent.instances[-1]
        # messages[0] 必须是新 system（旧 system 时变陈旧，不可回灌）
        assert agent.messages[0]["role"] == "system"
        assert agent.messages[0]["content"] != STALE_SYSTEM_TEXT
        # stored[1:] 原样保留
        assert agent.messages[1] == stored[1]
        assert agent.messages[2] == stored[2]
        # arguments 是 JSON 字符串，绝不再 json.loads 成 dict（gotcha 10）
        args = agent.messages[2]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        assert json.loads(args) == {"code": "600519", "days": 60}

    def test_get_messages_returns_stored_body(self, client, session_root):
        stored = stored_history_messages()
        write_session_file(session_root, "s-msgs", messages=stored, title="消息查看")
        r = client.get("/api/sessions/s-msgs/messages")
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == "s-msgs"
        assert body["title"] == "消息查看"
        messages = body["messages"]
        assert isinstance(messages, list) and len(messages) == len(stored)
        # stored[1:] 原样；arguments 仍是字符串
        assert messages[1] == stored[1]
        assert messages[2] == stored[2]
        args = messages[2]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        assert messages[0]["role"] == "system"


# ── 坏 JSON / cap 200 / 落盘内容 ───────────────────────────────────


class TestStoreRobustness:
    def test_bad_json_file_skipped(self, client, session_root):
        write_session_file(session_root, "s-good", title="正常会话")
        session_root.mkdir(parents=True, exist_ok=True)
        (session_root / "s-broken.json").write_text("{not valid json!!!", encoding="utf-8")
        r = client.get("/api/sessions")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()["sessions"]]
        assert "s-good" in ids
        assert "s-broken" not in ids

    def test_cap_200_evicts_oldest(self, client, session_root):
        # 预置 200 个合法会话（updated_at 递增），再建 1 个 → 淘汰最旧
        for i in range(200):
            write_session_file(
                session_root,
                f"s-{i:03d}",
                title=f"旧会话{i}",
                created_at=f"2026-01-01T00:00:{i % 60:02d}",
                updated_at=f"2026-02-{(i // 60) % 28 + 1:02d}T{i // 3600 % 24:02d}:{i % 60:02d}:00",
            )
        r = client.post("/api/sessions", json={"title": "第201个"})
        assert r.status_code == 200
        new_id = r.json()["session"]["id"]
        sessions = client.get("/api/sessions").json()["sessions"]
        assert len(sessions) == 200, f"cap 200 应淘汰最旧，实际 {len(sessions)}"
        ids = [s["id"] for s in sessions]
        assert new_id in ids
        assert "s-000" not in ids, "最旧会话应被淘汰"

    def test_persisted_file_contains_side_state(self, client, session_root):
        sid = client.post("/api/sessions", json={}).json()["session"]["id"]
        r = client.post("/api/chat", json={"session_id": sid, "message": "帮我看看贵州茅台"})
        assert r.status_code == 200
        data = read_session_file(session_root, sid)
        # 形状：id/title/created_at/updated_at/protocol/messages + 三个旁路状态
        for key in ("id", "title", "created_at", "updated_at", "protocol", "messages"):
            assert key in data, f"落盘缺 {key}"
        for key in ("user_messages", "session_stocks", "session_topics"):
            assert key in data, f"落盘缺旁路状态 {key}（只存 messages 会丢画像/摘要输入）"
        assert data["user_messages"] == ["帮我看看贵州茅台"]
        assert "600519" in list(data["session_stocks"])
        assert data["messages"], "done 后 messages 必须落盘"
        assert any(m.get("role") == "user" for m in data["messages"])
        assert data["protocol"] in ("openai", "anthropic")

    def test_save_idempotent_no_duplicate_growth(self, client, session_root):
        """幂等保存：同会话连跑两轮，文件是完整消息列表而不是重复追加垃圾"""
        sid = client.post("/api/sessions", json={}).json()["session"]["id"]
        for msg in ("第一问", "第二问"):
            r = client.post("/api/chat", json={"session_id": sid, "message": msg})
            assert r.status_code == 200
        data = read_session_file(session_root, sid)
        assert data["user_messages"] == ["第一问", "第二问"]
        user_msgs = [m for m in data["messages"] if m.get("role") == "user"]
        assert [m["content"] for m in user_msgs] == ["第一问", "第二问"]


# ── 分析进行中删除会话（M2）──────────────────────────────────────


class TestDeleteDuringAnalysis:
    def test_delete_mid_stream_no_revive_and_on_session_end(self, tmp_path):
        """M2：流式中 DELETE → 返回 ok 后文件不得被收尾 _persist 复活；on_session_end 被调用"""
        join_timeout = 15.0
        with make_test_client(tmp_path, agent_factory=HangingAgent) as client:
            root = tmp_path / "sessions"
            sid = client.post("/api/sessions", json={}).json()["session"]["id"]
            out: dict = {}

            def _run():
                out["resp"] = client.post("/api/chat", json={"session_id": sid, "message": "长分析"})

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            agent = None
            for _ in range(100):
                if HangingAgent.instances:
                    agent = HangingAgent.instances[-1]
                    break
                threading.Event().wait(0.05)
            assert agent is not None
            assert agent.started.wait(5.0), "HangingAgent 未进入流式"

            r = client.delete(f"/api/sessions/{sid}")
            assert r.status_code == 200
            t.join(join_timeout)
            assert not t.is_alive(), "流必须随 DELETE 取消结束"

            # 流结束后文件不得复活（收尾 _persist 必须跳过）
            assert not (root / f"{sid}.json").exists(), "DELETE 返回 ok 后文件被收尾 _persist 复活"
            # on_session_end 被调用（后台持锁收尾）
            for _ in range(100):
                if agent.on_session_end_calls >= 1:
                    break
                threading.Event().wait(0.05)
            assert agent.on_session_end_calls >= 1, "DELETE 必须触发 on_session_end"
            assert agent.on_session_end_forget is True, "删除会话必须 forget=True（不沉淀被删对话的记忆）"
            # 列表/404 语义即时生效
            assert client.get("/api/sessions").json()["sessions"] == []
            assert client.get(f"/api/sessions/{sid}/messages").status_code == 404

    def test_delete_after_stream_end_still_calls_on_session_end(self, client, tmp_path):
        """正常结束的会话删除后 on_session_end 照常调用（收尾不被锁协同卡死）"""
        sid = client.post("/api/sessions", json={}).json()["session"]["id"]
        r = client.post("/api/chat", json={"session_id": sid, "message": "短分析"})
        assert r.status_code == 200
        agent = FakeWebAgent.instances[-1]
        assert client.delete(f"/api/sessions/{sid}").status_code == 200
        for _ in range(100):
            if agent.on_session_end_calls >= 1:
                break
            threading.Event().wait(0.05)
        assert agent.on_session_end_calls >= 1
        assert agent.on_session_end_forget is True, "删除会话必须 forget=True（不沉淀被删对话的记忆）"
        assert not (tmp_path / "sessions" / f"{sid}.json").exists()


# ── protocol 不一致回灌（M10）────────────────────────────────────


class TestProtocolMismatchRestore:
    def test_mismatch_keeps_side_tables_skips_messages(self, client, session_root):
        """M10：protocol 不一致 → 消息体跳过回灌，但侧表必须保留且下一轮落盘不得抹掉"""
        sid = "proto-mismatch"
        write_session_file(
            session_root,
            sid,
            messages=stored_history_messages(),
            protocol="anthropic",  # 与 FakeWebAgent.client.protocol="openai" 不一致
            user_messages=["老问题：茅台现在能买吗"],
            session_stocks=["600519"],
            session_topics=["白酒"],
        )
        r = client.post("/api/chat", json={"session_id": sid, "message": "新问题"})
        assert r.status_code == 200
        agent = FakeWebAgent.instances[-1]
        assert agent.client.protocol != "anthropic", "用例前提：协议确实不一致"

        # 消息体跳过：陈旧 system / 旧问答都不得回灌
        assert agent.messages[0]["content"] == NEW_SYSTEM_TEXT
        assert all(m.get("content") != STALE_SYSTEM_TEXT for m in agent.messages)
        assert all(m.get("content") != "老问题：茅台现在能买吗" for m in agent.messages)
        assert all(m.get("content") != "老回答：先看K线。" for m in agent.messages)

        # 侧表保留：历史侧表 + 新一轮并存
        assert "老问题：茅台现在能买吗" in agent._user_messages
        assert "新问题" in agent._user_messages
        assert "600519" in agent._session_stocks
        assert "白酒" in agent._session_topics

        # 下一轮落盘不得永久抹掉历史侧表
        data = read_session_file(session_root, sid)
        assert "老问题：茅台现在能买吗" in data["user_messages"]
        assert "新问题" in data["user_messages"]
        assert "600519" in data["session_stocks"]
        assert "白酒" in data["session_topics"]

    def test_matched_protocol_still_restores_messages(self, client, session_root):
        """对照：协议一致时消息体照常回灌（M10 不得误伤正常回灌）"""
        sid = "proto-match"
        write_session_file(
            session_root, sid, messages=stored_history_messages(), protocol="openai"
        )
        r = client.post("/api/chat", json={"session_id": sid, "message": "新问题"})
        assert r.status_code == 200
        agent = FakeWebAgent.instances[-1]
        assert any(m.get("content") == "老问题：茅台现在能买吗" for m in agent.messages)
        assert any(m.get("content") == "老回答：先看K线。" for m in agent.messages)


# ── 非法 session_id 路径入口（m2）────────────────────────────────


class TestIllegalSessionIdEntries:
    def test_bad_sid_rejected_on_path_entries(self, client):
        """m2：PATCH/DELETE/messages 非法 id 一律 400 中文 detail（路径段用 ! . 空格）"""
        for bad in ("bad!id", "a.b", "a b"):
            r = client.patch(f"/api/sessions/{bad}", json={"title": "x"})
            assert r.status_code == 400, f"PATCH 非法 id {bad!r} 必须 400"
            assert "session_id" in r.json().get("detail", "")
            r = client.delete(f"/api/sessions/{bad}")
            assert r.status_code == 400, f"DELETE 非法 id {bad!r} 必须 400"
            assert "session_id" in r.json().get("detail", "")
            r = client.get(f"/api/sessions/{bad}/messages")
            assert r.status_code == 400, f"messages 非法 id {bad!r} 必须 400"
            assert "session_id" in r.json().get("detail", "")
