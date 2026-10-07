"""TUI 应用层测试：busy-guard/停止、会话保存、输入历史、评判闭环、工具展开

全部离线：_init_agents_async 替换为假实现；Agent/Judge 用假对象驱动；
输入历史与评测报告写入测试注入的 tmp 目录，绝不落真实 ~/.deeppulse。
"""

import asyncio
import json
from pathlib import Path

from textual.widgets import Button

from deeppulse.agent.tui.app import (
    DeepPulseTUI,
    extract_data_fields,
    fetch_needs_worker,
    load_history_file,
    parse_chart_args,
    save_history_file,
)
from deeppulse.agent.tui.widgets import ToolEntry


async def _fake_init(self):
    self._init_done = True
    self.status_bar.set_status("就绪")


class FakeAgent:
    """主 Agent 假对象：有限流式输出"""

    def __init__(self):
        self.messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        self.session_id = "test-session-0001"
        self._session_stocks = set()
        self._session_topics = set()
        self.reset_called = False
        self.session_end_called = False

    def reset(self):
        self.messages = []
        self.reset_called = True

    def on_session_end(self):
        self.session_end_called = True

    async def chat_stream_async(self, query):
        yield ("round", "1")
        yield ("content", "分析结果")
        yield ("tool_start", 'update_stock({"code": "600519"})')
        yield ("tool_result", '{"status": "ok", "price": 1856.0, "change_pct": 1.23}')
        yield ("done", "")


class HangingAgent(FakeAgent):
    """永不结束的流式输出（供停止/取消测试）"""

    async def chat_stream_async(self, query):
        yield ("content", "开始分析…")
        await asyncio.sleep(3600)
        yield ("done", "")


class FakeJudge:
    async def judge_stream_async(self, messages):
        yield ("content", "整体不错，评分：8/10\n风险提示略不足")
        yield ("score", "8")
        yield ("summary", json.dumps({"score": 8, "type": "综合分析", "timestamp": 1.0}, ensure_ascii=False))
        yield ("done", "")


class FakeEvent:
    def __init__(self, button_id: str):
        self.button = type("B", (), {"id": button_id})()


def _make_app(tmp_path, setting=None) -> DeepPulseTUI:
    app = DeepPulseTUI(setting=setting)
    app._history_file = Path(tmp_path) / "hist.json"
    app._judge_history_dir = str(Path(tmp_path) / "judge_history")
    return app


def _chat_plains(app) -> str:
    """对话流所有消息的纯文本拼接"""
    out = []
    for widget in app.chat_log.query("Static"):
        content = getattr(widget, "content", None)
        if content is not None:
            out.append(content.plain if hasattr(content, "plain") else str(content))
    return "\n".join(out)


# ═══════════════════ 纯函数 ═══════════════════


class TestExtractDataFields:
    def test_full_json(self):
        out = extract_data_fields('{"price": 1856.0, "change_pct": 1.23, "rsi": 32.1}')
        assert out["price"] == 1856.0
        assert out["rsi"] == 32.1

    def test_truncated_json_regex_fallback(self):
        out = extract_data_fields('{"status": "ok", "price": 1856.0, "change_pct": 1.2')
        assert out == {"price": "1856.0", "change_pct": "1.2"}

    def test_string_values(self):
        out = extract_data_fields('{"price": "1856.00"}')
        assert out == {"price": "1856.00"}

    def test_garbage(self):
        assert extract_data_fields("不是JSON") == {}
        assert extract_data_fields("") == {}


class TestFetchNeedsWorker:
    def test_worker_for_fetch_jobs(self):
        assert fetch_needs_worker("stocks")
        assert fetch_needs_worker("kline")
        assert fetch_needs_worker("all")
        assert fetch_needs_worker("update")

    def test_sync_for_instant_jobs(self):
        assert not fetch_needs_worker("")
        assert not fetch_needs_worker("help")
        assert not fetch_needs_worker("status")
        assert not fetch_needs_worker("帮助")


class TestParseChartArgs:
    def test_explicit_code_and_days(self):
        assert parse_chart_args("600519") == ("600519", 120)
        assert parse_chart_args("600519 60") == ("600519", 60)
        assert parse_chart_args("600519 0") == ("600519", 120)  # 非法天数回退默认

    def test_code_from_session_stocks(self):
        assert parse_chart_args("", ["白酒", "600519"]) == ("600519", 120)

    def test_missing_code(self):
        assert parse_chart_args("") is None
        assert parse_chart_args("", ["白酒"]) is None
        assert parse_chart_args("", None) is None


class TestHistoryFile:
    def test_roundtrip_and_bad_input(self, tmp_path):
        p = Path(tmp_path) / "sub" / "h.json"
        save_history_file(p, ["/help", "分析600519"])
        assert load_history_file(p) == ["/help", "分析600519"]
        assert load_history_file(Path(tmp_path) / "missing.json") == []
        bad = Path(tmp_path) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert load_history_file(bad) == []

    def test_capped(self, tmp_path):
        p = Path(tmp_path) / "h.json"
        save_history_file(p, [str(i) for i in range(500)])
        assert len(load_history_file(p)) == 200


# ═══════════════════ 会话保存 ═══════════════════


class TestSessionSave:
    def test_save_session_idempotent(self, tmp_path):
        app = _make_app(tmp_path)
        app.agent = FakeAgent()
        app._command_history = ["/help"]
        app._save_session()
        assert app.agent.session_end_called
        assert app._history_file.exists()
        # 幂等：重复退出不重复保存
        app.agent.session_end_called = False
        app._save_session()
        assert not app.agent.session_end_called

    def test_reset_saves_then_allows_resave(self, tmp_path):
        app = _make_app(tmp_path)
        app.agent = FakeAgent()

        async def run():
            # action_reset 需要面板句柄，直接验证核心语义
            app._save_session()
            assert app.agent.session_end_called
            app._session_saved = False  # 重置后开启新会话
            app.agent.session_end_called = False
            app._save_session()
            assert app.agent.session_end_called

        asyncio.run(run())


# ═══════════════════ busy-guard / 停止 / 历史 ═══════════════════


class TestBusyGuardAndStop:
    def test_second_submit_rejected_while_busy(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = _make_app(tmp_path)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.agent = HangingAgent()
                app._init_done = True

                app.chat_input.value = "分析贵州茅台"
                await pilot.press("enter")
                await pilot.pause(0.15)
                assert app._busy
                assert app.query_one("#stop-btn").has_class("-visible")

                app.chat_input.value = "再来一个"
                await pilot.press("enter")
                await pilot.pause(0.15)
                assert "仍在进行中" in _chat_plains(app)

        asyncio.run(run())

    def test_stop_cancels_active_analysis(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = _make_app(tmp_path)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.agent = HangingAgent()
                app._init_done = True

                app.chat_input.value = "分析贵州茅台"
                await pilot.press("enter")
                await pilot.pause(0.15)
                assert app._busy

                await pilot.press("ctrl+s")
                await pilot.pause(0.3)
                assert not app._busy
                assert not app.query_one("#stop-btn").has_class("-visible")
                assert "已停止" in _chat_plains(app)

                # 空闲时再停止：给出提示而不是崩溃
                await pilot.press("ctrl+s")
                await pilot.pause(0.1)
                assert "没有正在进行的分析" in _chat_plains(app)

        asyncio.run(run())

    def test_history_persisted_on_submit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = _make_app(tmp_path)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.agent = FakeAgent()
                app._init_done = True
                app.chat_input.value = "分析600519"
                await pilot.press("enter")
                await pilot.pause(0.2)
                saved = load_history_file(app._history_file)
                assert "分析600519" in saved

                # 重启后可恢复（模拟新 App 读取同一文件）
                app2 = _make_app(tmp_path)
                assert "分析600519" in load_history_file(app2._history_file)

        asyncio.run(run())

    def test_tab_completes_unique_command(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = _make_app(tmp_path)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.chat_input.value = "/ch"
                app.chat_input.focus()
                await app.action_complete_input()
                assert app.chat_input.value == "/chart "

                app.chat_input.value = "/c"  # /chart 与 /clear 均匹配
                await app.action_complete_input()
                assert app.chat_input.value == "/c"
                assert "可选命令" in _chat_plains(app)

        asyncio.run(run())


# ═══════════════════ 评判闭环 ═══════════════════


class TestJudgeLoop:
    def test_disabled_judge_rejects_command(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = _make_app(tmp_path, setting={"agent": {"enable_judge": False}})
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                assert not app._judge_enabled
                await app.handle_command("/judge")
                await pilot.pause(0.1)
                assert "已禁用" in _chat_plains(app)

        asyncio.run(run())

    def test_summary_consumed_and_accept_writes_report(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)
        learned = {}

        def fake_record_learning(**kwargs):
            learned.update(kwargs)

        from deeppulse.agent.tools import TOOL_DISPATCH

        monkeypatch.setitem(TOOL_DISPATCH, "record_learning", fake_record_learning)

        async def run():
            app = _make_app(tmp_path)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.agent = FakeAgent()
                app.judge_agent = FakeJudge()
                app.current_query = "贵州茅台短线怎么看"

                await app.run_judge_agent()
                await pilot.pause(0.1)
                assert app.judge_result["summary"]["score"] == 8
                assert app._confirm_buttons is not None

                await app.on_button_pressed(FakeEvent("confirm-accept"))
                await pilot.pause(0.1)

                # 报告正文已落盘
                files = list((Path(tmp_path) / "judge_history").glob("*.json"))
                assert len(files) == 1
                record = json.loads(files[0].read_text(encoding="utf-8"))
                assert "8/10" in record["report"]
                assert record["summary"]["score"] == 8
                # 学习记录带上评分
                assert "评分 8/10" in learned.get("learned_what", "")
                # 按钮已禁用防重复
                for btn in app._confirm_buttons.query(Button):
                    assert btn.disabled

        asyncio.run(run())

    def test_reject_disables_buttons(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = _make_app(tmp_path)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.agent = FakeAgent()
                app.judge_agent = FakeJudge()
                await app.run_judge_agent()
                await pilot.pause(0.1)
                await app.on_button_pressed(FakeEvent("confirm-reject"))
                await pilot.pause(0.1)
                assert "已忽略此次评判" in _chat_plains(app)
                for btn in app._confirm_buttons.query(Button):
                    assert btn.disabled

        asyncio.run(run())


# ═══════════════════ 工具条目展开 ═══════════════════


class TestToolEntryExpand:
    def test_click_toggles_full_result(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = _make_app(tmp_path)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                panel = app.tool_panel
                panel.add_tool_execution("update_stock", {"code": "600519"})
                # 摘要 44 字截断：标记文本只在展开视图出现
                panel.complete_tool_execution(
                    '{"status": "ok", "rows": 243, "note": "padding-padding-padding-padding", "extra": "EXPANDED_ONLY_MARKER"}'
                )
                await pilot.pause(0.2)

                entries = list(panel.query(ToolEntry))
                assert len(entries) == 1
                entry = entries[0]
                collapsed = entry.content.plain if hasattr(entry.content, "plain") else str(entry.content)
                assert "EXPANDED_ONLY_MARKER" not in collapsed  # 折叠只显示摘要
                assert "[展开]" in collapsed

                entry.on_click()
                await pilot.pause(0.1)
                expanded = entry.content.plain if hasattr(entry.content, "plain") else str(entry.content)
                assert "EXPANDED_ONLY_MARKER" in expanded
                assert "[点击收起]" in expanded

                entry.on_click()
                await pilot.pause(0.1)
                collapsed2 = entry.content.plain if hasattr(entry.content, "plain") else str(entry.content)
                assert "EXPANDED_ONLY_MARKER" not in collapsed2

        asyncio.run(run())
