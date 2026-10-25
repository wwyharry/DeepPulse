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
        with patch.dict("deeppulse.agent.agent.TOOL_DISPATCH", {"fast_tool": lambda **kw: '{"ok": true}'}, clear=False):
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


# ── 轮次预算：软上限/附加额度/硬上限强制收尾合成 ─────────────────────────


class BudgetLLM(FakeLLM):
    """带工具的调用按脚本返回 tool_calls；tools=None 的合成调用按配置返回文本/空/抛错。

    calls_tools[i] = 第 i+1 次调用收到的 tools 参数（None 即强制收尾合成轮）。
    """

    def __init__(
        self, tool_rounds: int = 10**6, synth_text: str = "合成结论", synth_mode: str = "text", text_at: int = None
    ):
        super().__init__()
        self.tool_rounds = tool_rounds  # 前 N 次带工具调用返回 tool_calls，之后返回文本
        self.text_at = text_at  # 指定第几次调用直接返回文本（续杯决策模拟）
        self.synth_text = synth_text
        self.synth_mode = synth_mode  # "text" / "empty" / "raise"
        self.calls_tools = []
        self.round = 0

    def _respond(self, tools):
        self.calls_tools.append(tools)
        self.round += 1
        if tools:
            if self.text_at is not None and self.round >= self.text_at:
                return {"content": "自然收尾结论", "tool_calls": None, "stop_reason": "stop"}
            if self.round <= self.tool_rounds:
                return {
                    "content": "",
                    "tool_calls": [{"id": f"c{self.round}", "name": "t1", "arguments": {}}],
                    "stop_reason": "tool_calls",
                }
            return {"content": "自然收尾结论", "tool_calls": None, "stop_reason": "stop"}
        # 强制收尾合成调用（tools=None）
        if self.synth_mode == "raise":
            raise RuntimeError("合成调用失败")
        if self.synth_mode == "empty":
            return {"content": "   ", "tool_calls": None, "stop_reason": "stop"}
        return {"content": self.synth_text, "tool_calls": None, "stop_reason": "stop"}

    def chat(self, messages, tools=None):
        self.received.append(list(messages))
        return self._respond(tools)

    def chat_stream(self, messages, tools=None):
        self.received.append(list(messages))
        resp = self._respond(tools)

        class Chunk:
            type = "content"
            text = resp.get("content") or ""

        yield Chunk()
        self.last_stream_response = resp

    async def chat_stream_async(self, messages, tools=None):
        self.received.append(list(messages))
        resp = self._respond(tools)

        class Chunk:
            type = "content"
            text = resp.get("content") or ""

        yield Chunk()
        self.last_stream_response = resp

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


_T1_TOOLS = {"t1": lambda **kw: '{"ok": 1}'}


class TestRoundBudgetLimits:
    def test_default_extension_when_attr_missing(self, mock_agent):
        # mock_agent 只有 max_rounds=10，无 max_rounds_extension → 默认 10
        assert mock_agent._round_limits() == (10, 10, 20)

    def test_zero_extension_disables(self, mock_agent):
        mock_agent.max_rounds_extension = 0
        assert mock_agent._round_limits() == (10, 0, 10)

    def test_invalid_extension_falls_back_default(self, mock_agent):
        for bad in (-1, "abc", 3.5, True):
            mock_agent.max_rounds_extension = bad
            assert mock_agent._round_limits() == (10, 10, 20), bad

    def test_extension_value_zero_is_kept(self):
        assert StockAgent._extension_value(0) == 0
        assert StockAgent._extension_value(7) == 7


class TestBudgetNotes:
    def _agent(self, base, ext):

        a = StockAgent.__new__(StockAgent)
        a.max_rounds = base
        a.max_rounds_extension = ext
        a._pending_system_notes = []
        a._wrapup_noted = False
        a.verbose = False
        a.client = FakeLLM()
        a._tool_call_history = []
        a._consecutive_tool_errors = 0
        a._truncated_tools = set()
        return a

    def test_boundary_note_fires_once(self):
        a = self._agent(3, 2)
        a._queue_budget_notes(4)  # 基础 3 轮用完，下一调用是第 4 轮
        assert len(a._pending_system_notes) == 1
        note = a._pending_system_notes[0]
        assert "基础轮次已用完（3 轮）" in note and "附加额度还剩 2 轮（硬上限 5 轮）" in note
        a._queue_budget_notes(5)  # 边界只注一次
        assert not any("基础轮次已用完" in n for n in a._pending_system_notes[1:])

    def test_wrapup_note_near_hard_cap(self):
        a = self._agent(3, 3)
        a._queue_budget_notes(5)  # remaining = 6-5+1 = 2
        assert any("轮次预算仅剩 2 轮" in n for n in a._pending_system_notes)
        a._pending_system_notes.clear()
        a._queue_budget_notes(6)  # 整 turn 至多一次
        assert not any("仅剩" in n for n in a._pending_system_notes)

    def test_no_double_fire_on_boundary_when_ext_small(self):
        a = self._agent(3, 1)  # 边界 next_round=4 与 remaining<=2 重合
        a._queue_budget_notes(4)
        assert len(a._pending_system_notes) == 1
        assert "基础轮次已用完" in a._pending_system_notes[0]

    def test_zero_extension_no_boundary_note(self):
        a = self._agent(3, 0)  # hard_cap=3：下一调用第 2 轮时 remaining=2
        a._queue_budget_notes(2)
        assert not any("基础轮次已用完" in n for n in a._pending_system_notes)
        # 但临近硬上限（=基础）仍有收尾预告
        assert any("仅剩" in n for n in a._pending_system_notes)


class TestForcedSynthesis:
    """硬上限处强制收尾：tools=None 合成轮把已收集数据写成结论，绝不丢工作"""

    def test_chat_synthesizes_at_hard_cap(self, mock_agent):
        client = BudgetLLM(tool_rounds=10**6)  # 永远要工具 → 必触硬上限
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 2
        mock_agent.max_rounds_extension = 0
        out = mock_agent.chat("问题")
        assert out == "合成结论"
        assert client.calls_tools[-1] is None, "最后一次调用必须 tools=None（合成轮）"
        assert client.calls_tools.count(None) == 1
        assert mock_agent.messages[-1]["role"] == "assistant"
        assert "合成结论" in mock_agent.messages[-1]["content"]
        assert "分析轮次已达上限" not in out

    def test_chat_stream_synthesizes_with_round_event(self, mock_agent):
        client = BudgetLLM(tool_rounds=10**6)
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 2
        mock_agent.max_rounds_extension = 1  # hard=3 → 合成轮 round 4
        events = list(mock_agent.chat_stream("问题"))
        rounds = [n for t, n in events if t == "round"]
        assert rounds == [1, 2, 3, 4], f"round 事件应含合成轮，实际 {rounds}"
        assert events[-1] == ("done", "")
        assert client.calls_tools[-1] is None
        contents = [t for typ, t in events if typ == "content"]
        assert any("合成结论" in c for c in contents)
        assert mock_agent.messages[-1]["role"] == "assistant"

    def test_chat_stream_async_synthesizes(self, mock_agent):
        import asyncio

        client = BudgetLLM(tool_rounds=10**6)
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 1
        mock_agent.max_rounds_extension = 1

        async def run():
            return [ev async for ev in mock_agent.chat_stream_async("问题")]

        events = asyncio.run(run())
        assert [n for t, n in events if t == "round"] == [1, 2, 3]
        assert client.calls_tools[-1] is None
        assert any(t == "content" and "合成结论" in txt for txt, t in [(e[1], e[0]) for e in events] if t == "content")

    def test_synthesis_failure_falls_back_to_canned(self, mock_agent):
        client = BudgetLLM(synth_mode="raise")
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 1
        mock_agent.max_rounds_extension = 0
        out = mock_agent.chat("问题")
        assert out == "分析轮次已达上限，请简化问题后重试。"
        assert mock_agent.messages[-1]["role"] == "assistant"

    def test_synthesis_empty_content_falls_back(self, mock_agent):
        client = BudgetLLM(synth_mode="empty")
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 1
        mock_agent.max_rounds_extension = 0
        events = list(mock_agent.chat_stream("问题"))
        contents = [t for typ, t in events if typ == "content"]
        assert any("分析轮次已达上限" in c for c in contents)
        assert events[-1] == ("done", "")

    def test_stream_synthesis_failure_does_not_leak_stale_response(self, mock_agent):
        """合成调用早期抛错：不得把上一轮残留 last_stream_response 当终稿写进历史"""
        client = BudgetLLM(tool_rounds=10**6, synth_mode="raise")
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 1
        mock_agent.max_rounds_extension = 0
        events = list(mock_agent.chat_stream("问题"))
        # 上一轮的 tool_calls 残留不得出现在终稿
        assert mock_agent.messages[-1]["role"] == "assistant"
        assert "分析轮次已达上限" in mock_agent.messages[-1]["content"]
        assert mock_agent.messages[-1].get("tool_calls") is None
        assert events[-1] == ("done", "")


class TestExtensionSelfSelection:
    """基础边界处由模型自主决定：继续调工具 = 续杯，直接答 = 自然收尾（无合成轮）"""

    def test_model_wraps_at_boundary(self, mock_agent):
        client = BudgetLLM(tool_rounds=2, text_at=3)  # 第 3 次调用（决策轮）直接作答
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 2
        mock_agent.max_rounds_extension = 3
        out = mock_agent.chat("问题")
        assert out == "自然收尾结论"
        assert client.calls_tools.count(None) == 0, "自然收尾不应触发合成轮"
        assert len(client.calls_tools) == 3
        # 决策轮收到的历史里必须有边界提示
        assert any("基础轮次已用完" in str(m.get("content", "")) for m in client.received[-1])
        assert mock_agent.messages[-1]["role"] == "assistant"

    def test_model_extends_at_boundary(self, mock_agent):
        client = BudgetLLM(tool_rounds=3, text_at=4)  # 决策轮继续要工具，第 4 次作答
        _ready_agent(mock_agent, client)
        mock_agent.max_rounds = 2
        mock_agent.max_rounds_extension = 3
        out = mock_agent.chat("问题")
        assert out == "自然收尾结论"
        assert client.calls_tools.count(None) == 0
        assert len(client.calls_tools) == 4
        # 边界后的续杯轮里仍有边界提示可见
        assert any("基础轮次已用完" in str(m.get("content", "")) for m in client.received[2])


class TestSmartTruncateCatalog:
    """工具输出结构化截断：因子目录剥说明保 id/参数、按类裁剪如实报数、按工具预算"""

    def _agent(self):
        a = StockAgent.__new__(StockAgent)
        a._pending_system_notes = []
        a._truncated_tools = set()
        return a

    @staticmethod
    def _catalog(n_cats=4, n_per=25):
        return {
            "categories": [
                {
                    "category": f"类{i}",
                    "factors": [
                        {
                            "id": f"f_{i}_{j}",
                            "name": f"因子{i}-{j}",
                            "description": "说明" + "长" * 100,
                            "params": {"fast": {"default": 5, "min": 2, "max": 60, "step": 1}},
                        }
                        for j in range(n_per)
                    ],
                }
                for i in range(n_cats)
            ]
        }

    def test_result_budget_override(self):
        from deeppulse import config

        assert StockAgent._result_budget("list_factors") == 16384
        assert StockAgent._result_budget("get_daily_kline") == config.MAX_TOOL_RESULT_CHARS == 8000

    def test_catalog_strip_descriptions_keeps_all_ids(self):
        a = self._agent()
        raw = json.dumps(self._catalog(), ensure_ascii=False)
        assert len(raw) > 8000, "样例须大过通用上限才有截断意义"
        out = a._smart_truncate(raw, StockAgent._result_budget("list_factors"), "list_factors")
        data = json.loads(out)
        assert len(out) <= StockAgent._result_budget("list_factors")
        assert data["_truncated"] is True
        assert "说明" in data["_truncated_hint"] and "list_factors" in data["_truncated_hint"]
        ids = [f["id"] for c in data["categories"] for f in c["factors"]]
        assert ids == [f"f_{i}_{j}" for i in range(4) for j in range(25)], "剥说明后因子 id 全保"
        assert all("description" not in f for c in data["categories"] for f in c["factors"])
        assert all(set(f) >= {"id", "name", "params"} for c in data["categories"] for f in c["factors"])

    def test_catalog_trims_factors_with_honest_counts(self):
        a = self._agent()
        cat = self._catalog()
        all_ids = {f["id"] for c in cat["categories"] for f in c["factors"]}
        raw = json.dumps(cat, ensure_ascii=False)
        out = a._smart_truncate(raw, 8000, "list_factors")
        data = json.loads(out)
        assert len(out) <= 8000
        kept = sum(len(c["factors"]) for c in data["categories"])
        assert 0 < kept < 100, "超预算须裁因子但不得全丢"
        assert f"{kept}/100" in data["_truncated_hint"], "hint 须报保留/总数"
        assert "list_factors" in data["_truncated_hint"], "hint 须指路按类取全量"
        for c in data["categories"]:
            for f in c["factors"]:
                assert set(f) >= {"id", "name", "params"}, "保留条目形态完整，不得腰斩成裸字符串"
                assert f["id"] in all_ids, "factor_id 必须是完整原 id（防截字符串腰斩）"
                assert "description" not in f

    def test_catalog_last_resort_keeps_structure(self):
        a = self._agent()
        cat = self._catalog(n_cats=30, n_per=40)
        all_ids = {f["id"] for c in cat["categories"] for f in c["factors"]}
        raw = json.dumps(cat, ensure_ascii=False)
        out = a._smart_truncate(raw, 1200, "list_factors")
        data = json.loads(out)
        assert len(out) <= 1200
        assert data["_truncated"] is True and "list_factors" in data["_truncated_hint"]
        assert isinstance(data["categories"], list) and data["categories"], "收窄后仍是合法目录结构"
        kept = sum(len(c["factors"]) for c in data["categories"])
        assert 0 < kept < 30 * 40, "至少每类留 1 个因子，不得裁成空目录"
        for c in data["categories"]:
            assert "category" in c
            assert c["factors"], "每个保留分类必须仍有因子"
            for f in c["factors"]:
                assert set(f) >= {"id", "name", "params"}
                assert f["id"] in all_ids, "factor_id 必须是完整原 id"

    def test_run_tool_respects_per_tool_budget(self):
        """预算须经 _run_tool 生产接缝生效——回退硬编码 8000 此钉变红"""
        import deeppulse.agent.agent as agent_mod

        a = self._agent()
        big = json.dumps(self._catalog(n_cats=1, n_per=60), ensure_ascii=False)
        assert 8000 < len(big) < 16384, "样例介于两个预算之间才有区分度"
        with patch.dict(
            agent_mod.TOOL_DISPATCH,
            {"list_factors": lambda **kw: big, "get_daily_kline": lambda **kw: big},
            clear=False,
        ):
            out_f = json.loads(a._run_tool("list_factors", {}))
            out_g = json.loads(a._run_tool("get_daily_kline", {}))
        ids = [f["id"] for c in out_f["categories"] for f in c["factors"]]
        assert len(ids) == 60 and out_f.get("_truncated") is not True, (
            "list_factors 走 16384 预算：介于两预算间的样例整体不触发截断"
        )
        assert out_g["_truncated"] is True, "其他工具仍受 8000 约束"
        assert len(json.dumps(out_g, ensure_ascii=False)) <= 8000
        g_ids = [f["id"] for c in out_g["categories"] for f in c["factors"]]
        assert g_ids and all(i in set(ids) for i in g_ids), "8000 预算收缩后 id 不得腰斩"
        assert all("description" not in f for c in out_g["categories"] for f in c["factors"]), (
            "8000 预算先剥说明（16384 预算的 list_factors 不必剥，此差即预算接线证据）"
        )

    def test_non_catalog_results_still_shrink_normally(self):
        a = self._agent()
        payload = {"results": [{"i": k, "s": "x" * 40} for k in range(400)]}
        raw = json.dumps(payload, ensure_ascii=False)
        assert len(raw) > 8000
        out = a._smart_truncate(raw, 8000, "get_daily_kline")
        data = json.loads(out)
        assert data["_truncated"] is True
        assert len(data["results"]) < 400, "非目录结构仍走识别键收缩"
        assert len(out) <= 8000
