"""Web 安全与密钥解析回归 —— 同源校验（CSRF）/ ${ENV_VAR} key 展开 / 单例 db_path 注入

对应审查缺陷：C1（settings/test 探活 key 外传）、M1（chat CSRF 烧配额/注会话）、
C2（env_ref key 不展开全 401）、M4（MemoryManager 忽略注入 db_path 写真实库）。
"""

from pathlib import Path

import pytest

from tests.fakes_web import make_test_client


@pytest.fixture
def client(tmp_path):
    with make_test_client(tmp_path) as c:
        yield c


class TestSameOriginGuard:
    """副作用接口拒绝浏览器跨站请求；同源/无 Origin 放行"""

    def test_cross_origin_chat_rejected(self, client):
        r = client.post(
            "/api/chat",
            json={"session_id": "s1", "message": "注入"},
            headers={"Origin": "http://evil.example", "Content-Type": "text/plain"},
        )
        assert r.status_code == 403
        assert "同源" in r.json()["detail"]

    def test_cross_origin_settings_test_rejected(self, client):
        """C1 PoC 路径：override.base_url 外传真实 key 的探活请求必须被挡"""
        r = client.post(
            "/api/settings/test",
            json={"llm": {"base_url": "https://attacker.example"}},
            headers={"Origin": "http://evil.example", "Content-Type": "text/plain"},
        )
        assert r.status_code == 403

    def test_cross_origin_put_settings_rejected(self, client):
        r = client.put(
            "/api/settings",
            json={"llm": {"model": "hacked"}},
            headers={"Origin": "http://evil.example", "Content-Type": "text/plain"},
        )
        assert r.status_code == 403

    def test_cross_origin_delete_rejected(self, client):
        r = client.delete("/api/sessions/nope", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403

    def test_null_origin_rejected(self, client):
        r = client.post("/api/chat/stop", json={"session_id": "s1"}, headers={"Origin": "null"})
        assert r.status_code == 403

    def test_same_origin_allowed(self, client):
        r = client.post("/api/chat/stop", json={"session_id": "s1"}, headers={"Origin": "http://testserver"})
        assert r.status_code == 200

    def test_no_origin_allowed(self, client):
        """无 Origin 头（curl/内部调用）放行——既有用例路径不受影响"""
        r = client.post("/api/chat/stop", json={"session_id": "s1"})
        assert r.status_code == 200

    def test_cross_origin_get_not_blocked(self, client):
        """只读 GET 不在校验面内（浏览器本就无法读取跨站响应）"""
        r = client.get("/api/health", headers={"Origin": "http://evil.example"})
        assert r.status_code == 200


class TestResolveApiKeyExpansion:
    """C2：${ENV_VAR} 形式 key 在 Web 运行路径必须展开（与 load_setting 同口径）"""

    def _pool_setting(self, api_key):
        from web.service.agent_pool import AgentPool

        pool = AgentPool({"llm": {"api_key": api_key, "model": "m", "protocol": "openai"}}, None, None)
        return pool._setting_for_agent()

    def test_env_ref_expanded_in_agent_setting(self, monkeypatch):
        monkeypatch.setenv("DP_TEST_KEY", "sk-REAL-12345678")
        s = self._pool_setting("${DP_TEST_KEY}")
        assert s["llm"]["api_key"] == "sk-REAL-12345678"

    def test_env_ref_missing_raises_chinese(self):
        import pytest

        with pytest.raises(ValueError, match="环境变量"):
            self._pool_setting("${DP_NO_SUCH_ENV_VAR}")

    def test_sk_literal_in_braces_unwrapped(self):
        s = self._pool_setting("${sk-abc12345}")
        assert s["llm"]["api_key"] == "sk-abc12345"

    def test_raw_key_untouched(self):
        s = self._pool_setting("sk-plain-key")
        assert s["llm"]["api_key"] == "sk-plain-key"

    def test_placeholder_rejected(self):
        import pytest

        with pytest.raises(ValueError, match="未配置"):
            self._pool_setting("sk-your-actual-api-key-here")

    def test_build_llm_setting_expands_and_strips_empty_override(self, monkeypatch):
        monkeypatch.setenv("DP_TEST_KEY2", "sk-ENV-9999")
        from web.service.settings_store import build_llm_setting

        raw = {"llm": {"api_key": "${DP_TEST_KEY2}", "model": "m", "protocol": "openai"}}
        # 空串 override = 不变（m4）：不得覆盖掉 env_ref key
        s = build_llm_setting(raw, {"llm": {"api_key": ""}})
        assert s["llm"]["api_key"] == "sk-ENV-9999"

    def test_build_llm_setting_override_nonempty_wins(self, monkeypatch):
        from web.service.settings_store import build_llm_setting

        raw = {"llm": {"api_key": "sk-OLD", "model": "m"}}
        s = build_llm_setting(raw, {"llm": {"api_key": "sk-NEW-KEY"}})
        assert s["llm"]["api_key"] == "sk-NEW-KEY"

    def test_original_setting_dict_not_mutated(self, monkeypatch):
        monkeypatch.setenv("DP_TEST_KEY3", "sk-ENV-3333")
        setting = {"llm": {"api_key": "${DP_TEST_KEY3}"}}
        from web.service.agent_pool import AgentPool

        AgentPool(setting, None, None)._setting_for_agent()
        assert setting["llm"]["api_key"] == "${DP_TEST_KEY3}"


class TestMemorySingletonDbPath:
    """M4：MemoryManager 必须吃注入的 db_path（测试绝不写真实库）"""

    def test_memory_uses_injected_db_path(self, tmp_path):
        from web.service.singletons import ServiceSingletons

        injected = Path(tmp_path) / "injected.duckdb"
        s = ServiceSingletons(db_path=injected, setting={})
        s.init()
        assert s.memory is not None
        assert Path(s.memory.db_path) == injected
