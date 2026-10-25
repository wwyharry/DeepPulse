"""Unit tests for agent/client.py — load_setting, tool format conversion."""

import json
from unittest.mock import patch

import pytest

from deeppulse.agent.client import LLMClient, StreamChunk, load_setting

# ── load_setting ────────────────────────────────────────────────────


class TestLoadSetting:
    def test_loads_from_file(self, tmp_path):
        setting = {
            "llm": {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-test-key",
                "model": "deepseek-v4-pro",
                "protocol": "openai",
                "max_tokens": 1024,
                "temperature": 0.3,
            },
            "embedding": {"provider": "none"},
            "agent": {"max_rounds": 5, "verbose": False},
        }
        setting_file = tmp_path / "setting.json"
        setting_file.write_text(json.dumps(setting), encoding="utf-8")

        # Mock the Path to return tmp_path as the project root
        with patch("deeppulse.agent.client.Path") as mock_path:
            # Path(__file__).parent.parent.parent should be tmp_path
            mock_path.return_value.parent.parent.parent = tmp_path
            result = load_setting()
        assert result["llm"]["api_key"] == "sk-test-key"

    def test_env_var_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "sk-from-env")
        setting = {
            "llm": {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "${MY_API_KEY}",
                "model": "deepseek-v4-pro",
                "protocol": "openai",
                "max_tokens": 1024,
                "temperature": 0.3,
            },
            "embedding": {"provider": "none"},
            "agent": {"max_rounds": 5, "verbose": False},
        }
        setting_file = tmp_path / "setting.json"
        setting_file.write_text(json.dumps(setting), encoding="utf-8")

        with patch("deeppulse.agent.client.Path") as mock_path:
            mock_path.return_value.parent.parent.parent = tmp_path
            result = load_setting()
        assert result["llm"]["api_key"] == "sk-from-env"


# ── StreamChunk ─────────────────────────────────────────────────────


class TestStreamChunk:
    def test_defaults(self):
        chunk = StreamChunk(type="content")
        assert chunk.type == "content"
        assert chunk.text == ""
        assert chunk.tool_calls == []

    def test_with_data(self):
        chunk = StreamChunk(type="tool_calls", tool_calls=[{"id": "1", "name": "test"}])
        assert len(chunk.tool_calls) == 1


# ── LLMClient format methods ────────────────────────────────────────


class TestLLMClientFormats:
    @pytest.fixture
    def openai_client(self):
        """Create an LLMClient with mocked init (protocol=openai)."""
        with patch.object(LLMClient, "_init_client"):
            client = LLMClient.__new__(LLMClient)
            client.protocol = "openai"
            client.llm_config = {"protocol": "openai"}
            return client

    @pytest.fixture
    def anthropic_client(self):
        """Create an LLMClient with mocked init (protocol=anthropic)."""
        with patch.object(LLMClient, "_init_client"):
            client = LLMClient.__new__(LLMClient)
            client.protocol = "anthropic"
            client.llm_config = {"protocol": "anthropic"}
            return client

    def test_convert_tools_to_anthropic(self, openai_client):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_stock",
                    "description": "Search for a stock",
                    "parameters": {
                        "type": "object",
                        "properties": {"keyword": {"type": "string"}},
                    },
                },
            }
        ]
        result = openai_client._convert_tools_to_anthropic(tools)
        assert len(result) == 1
        assert result[0]["name"] == "search_stock"
        assert result[0]["description"] == "Search for a stock"
        assert "input_schema" in result[0]

    def test_format_tool_result_openai(self, openai_client):
        msg = openai_client.format_tool_result_message("call_123", "search_stock", '{"result": "ok"}')
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_123"
        assert msg["content"] == '{"result": "ok"}'

    def test_format_tool_result_anthropic(self, anthropic_client):
        msg = anthropic_client.format_tool_result_message("call_123", "search_stock", '{"result": "ok"}')
        assert msg["role"] == "user"
        assert msg["content"][0]["type"] == "tool_result"
        assert msg["content"][0]["tool_use_id"] == "call_123"

    def test_format_assistant_message_openai(self, openai_client):
        response = {
            "content": "Hello",
            "tool_calls": [{"id": "tc1", "name": "test", "arguments": {}}],
            "stop_reason": "tool_calls",
        }
        msg = openai_client.format_assistant_message(response)
        assert msg["role"] == "assistant"
        assert msg["content"] == "Hello"
        assert len(msg["tool_calls"]) == 1

    def test_format_assistant_message_anthropic(self, anthropic_client):
        response = {
            "content": "Hello",
            "tool_calls": [{"id": "tc1", "name": "test", "arguments": {"q": "1"}}],
            "stop_reason": "tool_calls",
        }
        msg = anthropic_client.format_assistant_message(response)
        assert msg["role"] == "assistant"
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][1]["type"] == "tool_use"


# ── max_tokens / temperature 空值口径（设置页「清空 = 显式 null」不能炸 anthropic） ──


class TestResolveMaxTokens:
    """盘上 max_tokens=None（设置页清空落盘）时：
    openai 协议可缺省；anthropic 的 Messages API 必填，None 直接进 SDK 会
    `int * None` TypeError——必须回退默认 4096。"""

    def _client(self, protocol: str, llm_config: dict) -> LLMClient:
        c = LLMClient.__new__(LLMClient)
        c.protocol = protocol
        c.llm_config = llm_config
        return c

    def test_openai_null_missing_zero_invalid_all_none(self):
        for cfg in (
            {"model": "m", "max_tokens": None},  # 显式 null（设置页清空）
            {"model": "m"},  # 键缺失
            {"model": "m", "max_tokens": 0},
            {"model": "m", "max_tokens": -5},
            {"model": "m", "max_tokens": "abc"},
        ):
            assert self._client("openai", cfg)._resolve_max_tokens() is None, cfg

    def test_anthropic_null_missing_zero_invalid_fall_back_4096(self):
        for cfg in (
            {"model": "m", "max_tokens": None},
            {"model": "m"},
            {"model": "m", "max_tokens": 0},
            {"model": "m", "max_tokens": "abc"},
        ):
            assert self._client("anthropic", cfg)._resolve_max_tokens() == 4096, cfg

    def test_explicit_value_kept_on_both_protocols(self):
        assert self._client("openai", {"model": "m", "max_tokens": 2048})._resolve_max_tokens() == 2048
        assert self._client("anthropic", {"model": "m", "max_tokens": 2048})._resolve_max_tokens() == 2048

    def test_stream_kwargs_never_hand_none_to_anthropic_sdk(self):
        msgs = [{"role": "user", "content": "hi"}]
        kw = self._client(
            "anthropic", {"model": "m", "max_tokens": None, "temperature": None}
        )._build_anthropic_stream_kwargs(msgs, None)
        assert kw["max_tokens"] == 4096, f"anthropic 必填项不得是 None，实际 {kw['max_tokens']}"
        kw = self._client(
            "openai", {"model": "m", "max_tokens": None, "temperature": None}
        )._build_openai_stream_kwargs(msgs, None)
        assert kw["max_tokens"] is None  # openai 兼容协议允许缺省
        # temperature 键缺失不得 KeyError（旧实现 4 处硬索引 llm_config["temperature"]）
        kw = self._client("openai", {"model": "m"})._build_openai_stream_kwargs(msgs, None)
        assert kw["temperature"] is None
