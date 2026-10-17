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
            assert agent.max_history_tokens == 16000
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
                        "max_history_tokens": 0,
                        "history_keep_last_turns": 3,
                    }
                }
            )
            assert agent2.max_history_messages == 0
            assert agent2.max_history_chars == 1234
            assert agent2.max_history_tokens == 0
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


# ── 中断收尾 / 配对修复 / 卡死防护 ──────────────────────────────────────


class ToolCallLLM(FakeLLM):
    """首轮返回并行 tool_calls、次轮返回纯文本的假客户端"""

    def __init__(self):
        super().__init__()
        self.round = 0

    async def chat_stream_async(self, messages, tools=None):
        self.received.append(list(messages))
        self.round += 1

        class Chunk:
            type = "content"
            text = "思考中"

        yield Chunk()
        if self.round == 1:
            self.last_stream_response = {
                "content": "",
                "tool_calls": [
                    {"id": "c1", "name": "t1", "arguments": {}},
                    {"id": "c2", "name": "t2", "arguments": {}},
                ],
                "stop_reason": "tool_calls",
            }
        else:
            self.last_stream_response = {"content": "ok", "tool_calls": None, "stop_reason": "stop"}

    def format_assistant_message(self, response):
        msg = {"role": "assistant", "content": response.get("content") or ""}
        if response.get("tool_calls"):
            msg["tool_calls"] = [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": "{}"}}
                for tc in response["tool_calls"]
            ]
        return msg

    def format_tool_result_message(self, tool_call_id, tool_name, result):
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result}


def _ready_agent(agent, client):
    """补齐 mock_agent 缺失的轮内/预算属性"""
    agent.client = client
    agent.max_history_messages = 0  # 不折叠，聚焦配对
    agent.max_history_chars = 0
    agent.max_history_tokens = 0
    agent.history_keep_last_turns = 1
    agent._compaction_stats = {"folded_messages": 0, "folded_tool_calls": 0}
    agent._tool_call_history = []
    agent._consecutive_tool_errors = 0
    agent._truncated_tools = set()


class TestInterruptRepair:
    def test_cancel_mid_tools_repairs_pairing(self, mock_agent):
        """用户停止在工具阶段：悬空 tool_call 补合成结果 + assistant 占位收束"""
        import asyncio

        from deeppulse.agent.history import validate_pairing

        _ready_agent(mock_agent, ToolCallLLM())
        tools = {"t1": lambda **kw: '{"ok": 1}', "t2": lambda **kw: '{"ok": 2}'}

        async def run():
            gen = mock_agent.chat_stream_async("问题")
            async for ev in gen:
                if ev[0] == "tool_start":  # t1 结果已入库，t2 未执行 → 中断
                    break
            await gen.aclose()  # 模拟停止后生成器收尾

        with patch.dict("deeppulse.agent.agent.TOOL_DISPATCH", tools, clear=False):
            asyncio.run(run())

        assert validate_pairing(mock_agent.messages, "openai") == []
        synth = next(m for m in mock_agent.messages if m.get("tool_call_id") == "c2")
        assert "未执行" in synth["content"]
        assert mock_agent.messages[-1]["role"] == "assistant"
        assert "[分析中断]" in mock_agent.messages[-1]["content"]

    def test_next_turn_after_interrupt_sends_valid_history(self, mock_agent):
        """用户报告的场景：停止后下一问不再触发 tool_calls 400"""
        import asyncio

        from deeppulse.agent.history import validate_pairing

        client = ToolCallLLM()
        _ready_agent(mock_agent, client)
        tools = {"t1": lambda **kw: '{"ok": 1}', "t2": lambda **kw: '{"ok": 2}'}

        async def run():
            gen = mock_agent.chat_stream_async("问题一")
            async for ev in gen:
                if ev[0] == "tool_start":
                    break
            await gen.aclose()
            async for _ in mock_agent.chat_stream_async("问题二"):
                pass

        with patch.dict("deeppulse.agent.agent.TOOL_DISPATCH", tools, clear=False):
            asyncio.run(run())

        sent = client.received[-1]  # 第二问实际发出的历史
        assert validate_pairing(sent, "openai") == []
        assert {"role": "user", "content": "问题二"} in sent

    def test_prepare_llm_messages_repairs_stale_dangling(self, mock_agent):
        """安全网：任何残留悬空历史在发出前被修复（不依赖收尾钩子）"""
        from deeppulse.agent.history import validate_pairing

        _ready_agent(mock_agent, FakeLLM())
        mock_agent.messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "旧问题"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
            },
        ]
        mock_agent._prepare_llm_messages()
        assert validate_pairing(mock_agent.messages, "openai") == []
        assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in mock_agent.messages)

    def test_stale_finalize_does_not_touch_new_turn(self, mock_agent):
        """过期生成器延迟收尾不得干扰新 turn（新 tool_calls 可能在途）"""
        _ready_agent(mock_agent, FakeLLM())
        mock_agent._turn_gen = 2
        mock_agent.messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
            },
        ]
        before = [dict(m) for m in mock_agent.messages]
        mock_agent._finalize_partial_turn(turn_gen=1)  # 上一 turn 的过期收尾
        assert mock_agent.messages == before  # 原样不动

    def test_own_turn_finalize_appends_placeholder(self, mock_agent):
        _ready_agent(mock_agent, FakeLLM())
        mock_agent._turn_gen = 3
        mock_agent.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
        mock_agent._finalize_partial_turn(turn_gen=3)
        assert mock_agent.messages[-1]["role"] == "assistant"
        assert "[分析中断]" in mock_agent.messages[-1]["content"]


class TestToolTimeout:
    def test_tool_timeout_returns_error_json(self, mock_agent):
        """工具卡死：超时返回错误 JSON，不阻塞 ReAct 循环"""
        import time

        def slow_tool(**kw):
            time.sleep(1.0)
            return '{"ok": true}'

        _ready_agent(mock_agent, FakeLLM())
        with patch.dict("deeppulse.agent.agent.TOOL_DISPATCH", {"slow_tool": slow_tool}, clear=False):
            with patch("deeppulse.config.TOOL_EXEC_TIMEOUT", 0.1):
                t0 = time.time()
                result = mock_agent._run_tool("slow_tool", {})
                elapsed = time.time() - t0

        data = json.loads(result)
        assert "error" in data and "超时" in data["error"]
        assert elapsed < 0.8  # 远小于工具自身的 1s 睡眠

    def test_tool_normal_result_unaffected(self, mock_agent):
        _ready_agent(mock_agent, FakeLLM())
        with patch.dict(
            "deeppulse.agent.agent.TOOL_DISPATCH", {"fast_tool": lambda **kw: '{"ok": true}'}, clear=False
        ):
            with patch("deeppulse.config.TOOL_EXEC_TIMEOUT", 5):
                result = mock_agent._run_tool("fast_tool", {})
        assert json.loads(result) == {"ok": True}

    def test_tool_exception_still_error_json(self, mock_agent):
        _ready_agent(mock_agent, FakeLLM())

        def boom(**kw):
            raise RuntimeError("炸了")

        with patch.dict("deeppulse.agent.agent.TOOL_DISPATCH", {"boom": boom}, clear=False):
            with patch("deeppulse.config.TOOL_EXEC_TIMEOUT", 5):
                result = mock_agent._run_tool("boom", {})
        assert "炸了" in json.loads(result)["error"]


class TestRepeatDetection:
    """重复调用拦截：只拦读工具的成功重复，写工具与失败重试必须放行"""

    def _track(self, agent, name, args, result):
        agent._track_tool_result(name, args, result)

    def test_read_tool_success_repeat_gets_note(self, mock_agent):
        _ready_agent(mock_agent, FakeLLM())
        mock_agent._pending_system_notes = []
        self._track(mock_agent, "query_kline", {"code": "600519"}, '{"kline": []}')
        mock_agent._pending_system_notes.clear()
        self._track(mock_agent, "query_kline", {"code": "600519"}, '{"kline": []}')
        assert any("勿重复调用" in n for n in mock_agent._pending_system_notes)

    def test_write_tool_repeat_never_blocked(self, mock_agent):
        """用户反复加删自选、写操作重试：不得被'勿重复调用'堵死"""
        _ready_agent(mock_agent, FakeLLM())
        mock_agent._pending_system_notes = []
        for _ in range(3):
            self._track(mock_agent, "add_to_watchlist", {"code": "600519"}, '{"status": "ok"}')
        assert not any("勿重复调用" in n for n in mock_agent._pending_system_notes)

    def test_failed_call_retry_not_blocked(self, mock_agent):
        """上次同参调用失败：重试放行（否则一次失败操作永远无法生效）"""
        _ready_agent(mock_agent, FakeLLM())
        mock_agent._pending_system_notes = []
        self._track(mock_agent, "remove_from_watchlist", {"code": "600519"}, '{"error": "不在自选股中"}')
        mock_agent._pending_system_notes.clear()
        self._track(mock_agent, "remove_from_watchlist", {"code": "600519"}, '{"status": "ok"}')
        assert not any("勿重复调用" in n for n in mock_agent._pending_system_notes)

    def test_read_tool_failed_retry_not_blocked(self, mock_agent):
        _ready_agent(mock_agent, FakeLLM())
        mock_agent._pending_system_notes = []
        self._track(mock_agent, "query_kline", {"code": "600519"}, '{"error": "timeout"}')
        mock_agent._pending_system_notes.clear()
        self._track(mock_agent, "query_kline", {"code": "600519"}, '{"kline": []}')
        assert not any("勿重复调用" in n for n in mock_agent._pending_system_notes)
