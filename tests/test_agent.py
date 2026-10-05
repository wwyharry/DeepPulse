"""Unit tests for agent/agent.py — session info tracking, memory update checks, system notes."""

import json
from unittest.mock import MagicMock, patch

import pytest

from deeppulse.agent.agent import StockAgent


@pytest.fixture
def mock_agent():
    """Create a StockAgent with mocked LLM client and memory."""
    with (
        patch("deeppulse.agent.agent.LLMClient"),
        patch("deeppulse.agent.agent.MemoryManager") as MockMem,
        patch("deeppulse.agent.agent.get_strategy_loader") as MockStrat,
    ):
        mock_mem = MockMem.return_value
        mock_mem.get_context_block.return_value = ""
        mock_mem.detect_learning_signals.return_value = []

        mock_strat = MockStrat.return_value
        mock_strat.get_all_content_summary.return_value = ""

        with (
            patch("deeppulse.agent.agent.build_system_prompt", return_value="test prompt"),
            patch("deeppulse.agent.agent.detect_market_state", return_value=None),
        ):
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


class TestBuildSystemContent:
    def test_assembles_base_memory_strategy(self, mock_agent):
        mock_agent.memory.get_context_block.return_value = "- 记忆条目"
        with patch("deeppulse.agent.agent.get_strategy_loader") as MockStrat:
            MockStrat.return_value.get_all_content_summary.return_value = "- 战法甲"
            content = mock_agent._build_system_content()
        assert content.startswith("test prompt")
        assert "## 历史记忆" in content and "记忆条目" in content
        assert "## 可用短线战法一览" in content and "战法甲" in content

    def test_integrates_user_profile(self, mock_agent):
        mock_agent.memory.user_profile.get_profile.return_value = json.dumps(
            {"profile": {"trading_style": {"value": "低吸"}}, "total": 1}, ensure_ascii=False
        )
        with patch("deeppulse.agent.agent.detect_market_state", return_value="发酵期"):
            content = mock_agent._build_system_content()
        assert "## 当前市场状态: 发酵期" in content
        assert "## 用户画像适配" in content and "低吸" in content

    def test_bad_profile_json_degrades(self, mock_agent):
        mock_agent.memory.user_profile.get_profile.return_value = "not json"
        content = mock_agent._build_system_content()
        assert content.startswith("test prompt")
        assert "## 用户画像适配" not in content

    def test_refresh_updates_system_message(self, mock_agent):
        mock_agent.messages = [{"role": "system", "content": "old"}]
        mock_agent.refresh_memory_context()
        assert mock_agent.messages[0]["content"].startswith("test prompt")


# ── B7 上下文管理接线 ──────────────────────────────────────


class FakeLLM:
    """记录收到 messages 的假客户端（openai 协议）"""

    protocol = "openai"

    def __init__(self):
        self.received = []
        self.last_stream_response = None

    def chat(self, messages, tools=None):
        self.received.append(list(messages))
        return {"content": "ok", "tool_calls": None, "stop_reason": "stop"}

    def chat_stream(self, messages, tools=None):
        self.received.append(list(messages))

        class Chunk:
            type = "content"
            text = "ok"

        yield Chunk()
        self.last_stream_response = {"content": "ok", "tool_calls": None, "stop_reason": "stop"}

    async def chat_stream_async(self, messages, tools=None):
        self.received.append(list(messages))

        class Chunk:
            type = "content"
            text = "ok"

        yield Chunk()
        self.last_stream_response = {"content": "ok", "tool_calls": None, "stop_reason": "stop"}

    def format_assistant_message(self, response):
        return {"role": "assistant", "content": response.get("content") or ""}


def _seed_long_history(agent, n_turns=6):
    msgs = [{"role": "system", "content": "test"}]
    for t in range(n_turns):
        msgs.append({"role": "user", "content": f"问题{t}"})
        msgs.append({"role": "assistant", "content": f"回答{t}"})
    agent.messages = msgs
    return msgs


class TestHistoryBudgetConfig:
    def test_budget_value_semantics(self):
        assert StockAgent._budget_value(None, 60) == 60
        assert StockAgent._budget_value(-5, 60) == 60
        assert StockAgent._budget_value("abc", 60) == 60
        assert StockAgent._budget_value(0, 60) == 0  # 0 = 不限
        assert StockAgent._budget_value("30", 60) == 30

    def test_init_budget_defaults_and_overrides(self):
        with (
            patch("deeppulse.agent.agent.LLMClient") as MockClient,
            patch("deeppulse.agent.agent.MemoryManager") as MockMem,
            patch("deeppulse.agent.agent.get_strategy_loader") as MockStrat,
            patch("deeppulse.agent.agent.build_system_prompt", return_value="p"),
            patch("deeppulse.agent.agent.detect_market_state", return_value=None),
        ):
            MockClient.return_value.setting = {"agent": {"verbose": False, "max_rounds": 10}}
            MockMem.return_value.get_context_block.return_value = ""
            MockMem.return_value.user_profile.get_profile.return_value = "{}"
            MockStrat.return_value.get_all_content_summary.return_value = ""
            # 缺新键 → 默认值
            agent = StockAgent()
            assert agent.max_history_messages == 60
            assert agent.max_history_chars == 40000
            assert agent.history_keep_last_turns == 1
            assert agent._compaction_stats == {"folded_messages": 0, "folded_tool_calls": 0}
            # 显式值生效（0 = 不限）
            agent2 = StockAgent(
                setting={
                    "agent": {
                        "verbose": False,
                        "max_rounds": 10,
                        "max_history_messages": 0,
                        "max_history_chars": 1234,
                        "history_keep_last_turns": 3,
                    }
                }
            )
            assert agent2.max_history_messages == 0
            assert agent2.max_history_chars == 1234
            assert agent2.history_keep_last_turns == 3


class TestCompactionWiring:
    def test_chat_folds_before_llm_call(self, mock_agent):
        mock_agent.client = FakeLLM()
        mock_agent.max_history_messages = 8
        mock_agent.max_history_chars = 0
        mock_agent.history_keep_last_turns = 1
        mock_agent._tool_call_history = []
        mock_agent._consecutive_tool_errors = 0
        mock_agent._truncated_tools = set()
        msgs = _seed_long_history(mock_agent, n_turns=6)

        mock_agent.chat("新问题")

        received = mock_agent.client.received[0]
        assert len(received) < len(msgs) + 1  # 发送的是折叠后列表
        assert {"role": "user", "content": "新问题"} in received
        assert {"role": "user", "content": "问题1"} not in received  # 旧轮已折叠
        assert received[0] is msgs[0]  # system 主提示同一引用
        assert mock_agent.messages[0] is msgs[0]
        assert mock_agent._compaction_stats["folded_messages"] > 0

    def test_chat_stream_folds_before_llm_call(self, mock_agent):
        mock_agent.client = FakeLLM()
        mock_agent.max_history_messages = 8
        mock_agent.max_history_chars = 0
        mock_agent.history_keep_last_turns = 1
        mock_agent._tool_call_history = []
        mock_agent._consecutive_tool_errors = 0
        mock_agent._truncated_tools = set()
        _seed_long_history(mock_agent, n_turns=6)

        list(mock_agent.chat_stream("新问题"))

        received = mock_agent.client.received[0]
        assert len(received) < 15
        assert {"role": "user", "content": "问题1"} not in received

    def test_chat_stream_async_folds_before_llm_call(self, mock_agent):
        import asyncio

        mock_agent.client = FakeLLM()
        mock_agent.max_history_messages = 8
        mock_agent.max_history_chars = 0
        mock_agent.history_keep_last_turns = 1
        mock_agent._tool_call_history = []
        mock_agent._consecutive_tool_errors = 0
        mock_agent._truncated_tools = set()
        _seed_long_history(mock_agent, n_turns=6)

        async def run():
            async for _ in mock_agent.chat_stream_async("新问题"):
                pass

        asyncio.run(run())
        received = mock_agent.client.received[0]
        assert len(received) < 15
        assert {"role": "user", "content": "问题1"} not in received

    def test_session_summary_survives_fold(self, mock_agent):
        # 早期用户消息已被折叠出 messages，摘要仍从前 3 个用户问题生成
        mock_agent._user_messages = ["第一问", "第二问", "第三问", "第四问"]
        mock_agent.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "第四问"}]
        summary = mock_agent._generate_session_summary()
        assert "第一问" in summary and "第三问" in summary

    def test_reset_clears_compaction_stats(self, mock_agent):
        mock_agent._tool_call_history = []
        mock_agent._consecutive_tool_errors = 0
        mock_agent._truncated_tools = set()
        mock_agent._compaction_stats = {"folded_messages": 9, "folded_tool_calls": 3}
        mock_agent.memory.user_profile.extract_from_conversation.return_value = {}
        mock_agent.reset()
        assert mock_agent._compaction_stats == {"folded_messages": 0, "folded_tool_calls": 0}
        assert len(mock_agent.messages) == 1 and mock_agent.messages[0]["role"] == "system"
