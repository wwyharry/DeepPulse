"""TUI 控件与布局测试

覆盖 resize 卡死修复与响应式布局：
- CachedContent 换行缓存（布局期零重复排版）
- 断点切换侧栏显隐（hide-left < 100 列 / hide-right < 78 列 / compact < 58 列）
- 大量长消息下 resize 洪泛的单次重排耗时预算（回归护栏）
- 状态栏窄窗口降级、消息类型与 Markdown 内容渲染

全部离线运行：_init_agents_async 一律替换为假实现（不建 Agent、不联网）。
"""

import asyncio
import time

from textual.screen import Screen

from deeppulse.agent.tui import palette as P
from deeppulse.agent.tui.app import DeepPulseTUI
from deeppulse.agent.tui.markdown_text import _display_width
from deeppulse.agent.tui.widgets import (
    CachedContent,
    ToolPanel,
    _change_style,
    _pad_label,
)


async def _fake_init(self):
    """替代真实 Agent 初始化（不联网、不建库连接）"""
    self._init_done = True
    self.status_bar.set_model("deepseek-v4-pro")
    self.status_bar.set_session("abcd1234")
    self.status_bar.set_status("就绪")


def _plain(widget) -> str:
    content = widget.content
    return content.plain if hasattr(content, "plain") else str(content)


class TestCachedContent:
    """换行结果按宽度缓存：同宽度命中同一对象，布局期不再重复排版"""

    def test_wrap_cached_per_width(self):
        c = CachedContent("hello world foo bar baz", [])
        lines_a = c._wrap_and_format(12)
        lines_b = c._wrap_and_format(12)
        assert lines_a is lines_b  # 命中缓存返回同一对象
        lines_c = c._wrap_and_format(16)
        assert lines_c is not lines_a
        assert len(c._wrap_cache) == 2

    def test_wrap_cache_bounded(self):
        c = CachedContent("x" * 50, [])
        for w in (10, 12, 14, 16, 18, 20):
            c._wrap_and_format(w)
        assert len(c._wrap_cache) == 4  # FIFO 上限
        assert (10, "left", "fold", False, 0, 8) not in c._wrap_cache  # 最旧被淘汰

    def test_selection_bypasses_cache(self):
        from textual.content import Selection

        c = CachedContent("hello world", [])
        c._wrap_and_format(8, selection=Selection((0, 0), (0, 2)))  # 选区变化需真实排版
        assert not c._wrap_cache  # 选区路径不写缓存
        c._wrap_and_format(8)
        assert len(c._wrap_cache) == 1

    def test_get_height_stable(self):
        c = CachedContent("第一行内容\n第二行内容较长需要折行处理" * 3, [])
        h1 = c.get_height({}, 20)
        h2 = c.get_height({}, 20)
        assert h1 == h2 > 0


class TestWidgetHelpers:
    def test_pad_label_cjk(self):
        assert _display_width(_pad_label("涨跌幅", 8)) == 8
        assert _display_width(_pad_label("RSI", 8)) == 8

    def test_change_style_up_down_flat(self):
        # A 股惯例：涨红跌绿
        assert _change_style("+1.23%") == (P.UP, "▲")
        assert _change_style("-0.5%") == (P.DOWN, "▼")
        assert _change_style("0.00%") == (P.TEXT_DIM, "●")
        assert _change_style("停牌") == (P.TEXT_DIM, "●")

    def test_result_hint_json_flattened(self):
        hint = ToolPanel._result_hint('{"status": "ok", "rows": 243}')
        assert hint == "status=ok, rows=243"
        assert '"' not in ToolPanel._result_hint('{"rsi": 32.1}')

    def test_content_strips_trailing_newlines(self):
        from rich.text import Text

        from deeppulse.agent.tui.widgets import _content

        assert _content(Text("hello\n\n\n")).plain == "hello"


class TestResponsiveLayout:
    """断点驱动侧栏显隐（Textual 无 @media，由 _apply_responsive 切换 Screen 类）"""

    def test_breakpoints_toggle_sidebars(self, monkeypatch):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = DeepPulseTUI()
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                left = app.query_one("#left-panel")
                right = app.query_one("#right-panel")
                assert "hide-left" not in app.screen.classes
                assert "hide-right" not in app.screen.classes
                assert left.display and right.display

                await pilot.resize_terminal(92, 44)
                await pilot.pause(0.3)
                assert "hide-left" in app.screen.classes
                assert "hide-right" not in app.screen.classes
                assert not left.display and right.display

                await pilot.resize_terminal(68, 40)
                await pilot.pause(0.3)
                assert "hide-left" in app.screen.classes
                assert "hide-right" in app.screen.classes
                assert not left.display and not right.display

                await pilot.resize_terminal(50, 30)
                await pilot.pause(0.3)
                assert "compact" in app.screen.classes

                await pilot.resize_terminal(150, 44)
                await pilot.pause(0.3)
                assert "hide-left" not in app.screen.classes
                assert "hide-right" not in app.screen.classes
                assert "compact" not in app.screen.classes
                assert left.display and right.display

        asyncio.run(run())


class TestResizePerformance:
    """resize 洪泛下单次重排耗时预算（修复前 Rich Markdown 重渲染是 O(消息数×文档长度)）"""

    def test_resize_flood_relayout_budget(self, monkeypatch):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = DeepPulseTUI()
            async with app.run_test(size=(150, 52)) as pilot:
                await pilot.pause(0.3)
                md = "## 分析要点\n\n" + "\n\n".join(
                    f"{i + 1}. **要点**：趋势、量能、资金面共振判断，" * 6 for i in range(6)
                )
                for i in range(16):
                    container = app.chat_log.add_agent_container("主分析")
                    container.update_content(md + f"\n\n第 {i + 1} 轮")
                    container.mark_complete()
                await pilot.pause(0.5)

                stats = {"t": 0.0, "n": 0}
                orig = Screen._refresh_layout

                def timed(self, *args, **kwargs):
                    t0 = time.perf_counter()
                    try:
                        return orig(self, *args, **kwargs)
                    finally:
                        stats["t"] += time.perf_counter() - t0
                        stats["n"] += 1

                monkeypatch.setattr(Screen, "_refresh_layout", timed)
                widths = [148, 130, 110, 95, 85, 75, 65, 55, 65, 75, 85, 95, 110, 130, 148]
                for w in widths:
                    await pilot.resize_terminal(w, 52)
                    await pilot.pause(0.05)
                n = max(stats["n"], 1)
                avg = stats["t"] / n
                assert avg < 0.05, f"单次重排均值 {avg * 1000:.1f}ms 超预算（{stats['n']} 次，总 {stats['t']:.2f}s）"

        asyncio.run(run())


class TestStatusBarAdaptive:
    """窄窗口丢弃次要中段（品牌 + 状态保底，轮次保留）"""

    def test_drops_segments_when_narrow(self, monkeypatch):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = DeepPulseTUI()
            async with app.run_test(size=(150, 30)) as pilot:
                await pilot.pause(0.3)
                sb = app.status_bar
                sb.set_model("deepseek-v4-pro")
                sb.set_session("abcd1234")
                sb.update_round(3)
                sb.update_elapsed(14.2)
                for _ in range(6):
                    sb.increment_tool_count()
                await pilot.pause(0.2)
                wide = _plain(sb)
                assert "次工具" in wide
                assert "14.2s" in wide
                assert "abcd1234" in wide

                await pilot.resize_terminal(56, 30)
                await pilot.pause(0.3)
                narrow = _plain(sb)
                assert "DeepPulse" in narrow
                assert "就绪" in narrow
                assert "R3" in narrow  # 轮次保留
                assert "次工具" not in narrow  # 先丢工具数
                assert "14.2s" not in narrow
                assert "abcd1234" not in narrow

        asyncio.run(run())


class TestChatLogMessages:
    def test_message_types_and_markdown_render(self, monkeypatch):
        monkeypatch.setattr(DeepPulseTUI, "_init_agents_async", _fake_init)

        async def run():
            app = DeepPulseTUI()
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                chat = app.chat_log
                chat.clear()
                chat.add_user_message("分析一下贵州茅台")
                chat.add_system_message("系统提示")
                chat.add_session_stats({"rounds": 2, "tool_count": 4, "elapsed": 9.5, "tools_used": ["a", "b"]})
                container = chat.add_agent_container("主分析")
                container.tools = ["update_stock"]
                container.update_content(
                    "## 结论\n\n- 看多 **MA5** 上方\n\n| 指标 | 值 |\n| --- | --- |\n| RSI | 32.1 |"
                )
                container.mark_complete()
                await pilot.pause(0.3)

                assert len(chat.query(".msg-user")) == 1
                assert len(chat.query(".msg-system")) == 1
                assert len(chat.query(".msg-stats")) == 1
                assert len(chat.query(".msg-agent")) == 1

                body = _plain(container)
                assert "主分析" in body
                assert "• 看多" in body  # markdown 列表已转 Text
                assert "─" in body  # 表格分隔线
                assert "MA5" in body
                assert "▍" not in body  # 完成后无流式光标

        asyncio.run(run())
