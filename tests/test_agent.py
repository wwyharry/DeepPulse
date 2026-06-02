"""Unit tests for agent/agent.py — session info tracking, memory update checks, system notes."""

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.agent import StockAgent


@pytest.fixture
def mock_agent():
    """Create a StockAgent with mocked LLM client and memory."""
    with (
        patch("agent.agent.LLMClient"),
        patch("agent.agent.MemoryManager") as MockMem,
        patch("agent.agent.get_strategy_loader") as MockStrat,
    ):
        mock_mem = MockMem.return_value
        mock_mem.get_context_block.return_value = ""
        mock_mem.detect_learning_signals.return_value = []

        mock_strat = MockStrat.return_value
        mock_strat.get_all_content_summary.return_value = ""

        with patch("agent.agent.SYSTEM_PROMPT", "test prompt"):
            agent = StockAgent.__new__(StockAgent)
            agent.memory = mock_mem
            agent.session_id = "test-session"
            agent.verbose = False
            agent.max_rounds = 10
            agent._user_messages = []
            agent._session_stocks = set()
            agent._session_topics = set()
            agent._pending_system_notes = []
            agent.messages = [{"role": "system", "content": "test"}]
            yield agent


class TestTrackSessionInfo:
    def test_extracts_stock_codes(self, mock_agent):
        # Stock code regex \b requires word boundary; space after code works
        mock_agent._track_session_info("帮我看看 600519 贵州茅台和 000001 平安银行")
        assert "600519" in mock_agent._session_stocks
        assert "000001" in mock_agent._session_stocks

    def test_extracts_stock_names(self, mock_agent):
        mock_agent._track_session_info("分析一下贵州茅台银行的走势")
        # The regex matches Chinese chars + 银行 suffix
        assert any("银行" in s for s in mock_agent._session_stocks)

    def test_extracts_topic_keywords(self, mock_agent):
        mock_agent._track_session_info("短线怎么看，有没有放量突破")
        assert "短线" in mock_agent._session_topics
        assert "放量" in mock_agent._session_topics
        assert "突破" in mock_agent._session_topics

    def test_no_codes_no_topics(self, mock_agent):
        mock_agent._track_session_info("今天天气怎么样")
        assert len(mock_agent._session_stocks) == 0
        assert len(mock_agent._session_topics) == 0

    def test_appends_user_message(self, mock_agent):
        mock_agent._track_session_info("测试消息")
        assert "测试消息" in mock_agent._user_messages

    def test_learning_signal_appends_note(self, mock_agent):
        mock_agent.memory.detect_learning_signals.return_value = [{"type": "纠错"}]
        mock_agent._track_session_info("你分析错了")
        assert len(mock_agent._pending_system_notes) == 1
        assert "纠错" in mock_agent._pending_system_notes[0]


class TestCheckMemoryUpdate:
    def test_saved_memory_appends_note(self, mock_agent):
        result = json.dumps({"status": "saved", "memory_type": "insight", "memory_id": "m1"})
        mock_agent._check_memory_update("save_memory", result)
        assert len(mock_agent._pending_system_notes) == 1
        assert "insight" in mock_agent._pending_system_notes[0]

    def test_learning_memory_triggers_refresh(self, mock_agent):
        mock_agent.refresh_memory_context = MagicMock()
        result = json.dumps({"status": "saved", "memory_type": "learning", "memory_id": "m2"})
        mock_agent._check_memory_update("save_memory", result)
        mock_agent.refresh_memory_context.assert_called_once()

    def test_non_save_tool_ignored(self, mock_agent):
        mock_agent._check_memory_update("search_stock", '{"results": []}')
        assert len(mock_agent._pending_system_notes) == 0

    def test_invalid_json_ignored(self, mock_agent):
        mock_agent._check_memory_update("save_memory", "not json")
        assert len(mock_agent._pending_system_notes) == 0

    def test_non_saved_status_ignored(self, mock_agent):
        result = json.dumps({"status": "error", "message": "failed"})
        mock_agent._check_memory_update("save_memory", result)
        assert len(mock_agent._pending_system_notes) == 0


class TestAppendPendingSystemNotes:
    def test_appends_and_clears(self, mock_agent):
        mock_agent._pending_system_notes = ["note1", "note2"]
        mock_agent._append_pending_system_notes()
        assert len(mock_agent.messages) == 3  # system + note1 + note2
        assert mock_agent.messages[1]["role"] == "system"
        assert mock_agent.messages[1]["content"] == "note1"
        assert mock_agent.messages[2]["content"] == "note2"
        assert len(mock_agent._pending_system_notes) == 0

    def test_no_notes_no_change(self, mock_agent):
        original_len = len(mock_agent.messages)
        mock_agent._append_pending_system_notes()
        assert len(mock_agent.messages) == original_len
