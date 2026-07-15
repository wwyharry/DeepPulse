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

        with patch("agent.client.Path") as mock_path:
            mock_path.return_value.parent.parent = tmp_path
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

        with patch("agent.client.Path") as mock_path:
            mock_path.return_value.parent.parent = tmp_path
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
