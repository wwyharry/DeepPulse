"""TUI界面核心组件 - 暗色金融主题

渲染约定：
- 面板/消息的边框、留白全部由 styles.tcss 控制（Python 侧不再写 DEFAULT_CSS）；
- 正文一律构建为 rich.text.Text，布局期只做换行（Markdown 在写入时经
  markdown_text.markdown_to_text 一次性转换），避免 resize 时反复重渲染；
- 涨红跌绿（A股习惯），色值见 palette.py。
"""

import time
from datetime import datetime

from rich.text import Text
from textual.containers import Container, ScrollableContainer
from textual.content import Content
from textual.widgets import Button, Static

from deeppulse.agent.tui import palette as P
from deeppulse.agent.tui.markdown_text import markdown_to_text

# Markdown 渲染节流间隔（秒），防止流式输出时 O(n²) 重渲染
_RENDER_THROTTLE = 0.2


def _label(text: str, style: str = "") -> Text:
    """快捷构造带样式的 Text 片段"""
    return Text(text, style=style)


def _pad_label(label: str, width: int = 8) -> str:
    """按显示宽度补齐标签（CJK 双宽），让右侧数值对齐"""
    import unicodedata

    w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in label)
    return label + " " * max(0, width - w)


class CachedContent(Content):
    """带「宽度 → 排版行」缓存的 Content。

    Textual 的 Content 每次 paint 都重新排版（_wrap_and_format），且 resize
    连发时窗口每变一次就全量重排一次——这是拖动窗口卡死的直接原因。
    这里缓存同一内容在各宽度下的排版结果（无选区时），测量与绘制共享。
    """

    _MAX_CACHE = 4

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._wrap_cache: dict[tuple, list] = {}

    def _wrap_and_format(
        self,
        width,
        align="left",
        overflow="fold",
        no_wrap=False,
        line_pad=0,
        tab_size=8,
        selection=None,
        selection_style=None,
        post_style=None,
        get_style=None,
    ):
        if selection is not None:
            return super()._wrap_and_format(
                width,
                align=align,
                overflow=overflow,
                no_wrap=no_wrap,
                line_pad=line_pad,
                tab_size=tab_size,
                selection=selection,
                selection_style=selection_style,
                post_style=post_style,
                get_style=get_style,
            )
        key = (width, align, overflow, no_wrap, line_pad, tab_size)
        lines = self._wrap_cache.get(key)
        if lines is None:
            kwargs = dict(
                align=align,
                overflow=overflow,
                no_wrap=no_wrap,
                line_pad=line_pad,
                tab_size=tab_size,
            )
            if get_style is not None:
                kwargs["get_style"] = get_style
            if post_style is not None:
                kwargs["post_style"] = post_style
            lines = super()._wrap_and_format(width, **kwargs)
            if len(self._wrap_cache) >= self._MAX_CACHE:
                self._wrap_cache.pop(next(iter(self._wrap_cache)))
            self._wrap_cache[key] = lines
        return lines

    def get_height(self, rules, width: int) -> int:
        line_pad = rules.get("line_pad", 0) * 2
        overflow = rules.get("text_overflow", "fold")
        no_wrap = rules.get("text_wrap", "wrap") == "nowrap"
        return len(self._wrap_and_format(width - line_pad, overflow=overflow, no_wrap=no_wrap))


def _content(body: Text) -> Content:
    """Text → CachedContent（一次转换，之后按宽度缓存排版结果）"""
    plain = body.plain
    trailing = len(plain) - len(plain.rstrip("\n"))
    if trailing:
        body = body[: len(plain) - trailing]
    base = Content.from_rich_text(body)
    return CachedContent(base.plain, list(base.spans))


# ═══════════════════ 状态栏 ═══════════════════


class StatusBar(Static):
    """顶部状态栏：品牌 | 模型 | 会话 | 轮次 | 耗时 | 工具数 | 状态"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_name = ""
        self.session_id = ""
        self.round_num = 0
        self.elapsed = 0.0
        self.tool_count = 0
        self.status_text = "就绪"
        self.refresh_display()

    def set_model(self, model: str):
        self.model_name = model
        self.refresh_display()

    def set_session(self, session_id: str):
        self.session_id = session_id[:8] if session_id else ""
        self.refresh_display()

    def update_round(self, round_num: int):
        self.round_num = round_num
        self.refresh_display()

    def update_elapsed(self, elapsed: float):
        self.elapsed = elapsed
        self.refresh_display()

    def increment_tool_count(self):
        self.tool_count += 1
        self.refresh_display()

    def set_status(self, text: str):
        self.status_text = text
        self.refresh_display()

    def reset_session_stats(self):
        self.round_num = 0
        self.elapsed = 0.0
        self.tool_count = 0
        self.refresh_display()

    def on_resize(self, event) -> None:
        # 尺寸变化后按新宽度重排中段（窄窗口丢弃次要信息）
        self.refresh_display()

    def refresh_display(self):
        sep = _label("  ·  ", f"dim {P.TEXT_FAINT}")
        brand = _label(" DeepPulse", f"bold {P.ACCENT}")
        ready = self.status_text == "就绪"
        status = _label(self.status_text, P.TEXT if ready else f"bold {P.WARN}")

        # 中段：[模型, 会话, 轮次, 耗时, 工具数]；窄窗口按 4→3→1→0 丢弃，轮次保留
        mids: list = [None, None, None, None, None]
        if self.model_name:
            short_model = self.model_name.split("/")[-1] if "/" in self.model_name else self.model_name
            mids[0] = _label(short_model, f"dim {P.TEXT_DIM}")
        if self.session_id:
            mids[1] = _label(self.session_id, f"dim {P.TEXT_DIM}")
        if self.round_num > 0:
            mids[2] = _label(f"R{self.round_num}", f"bold {P.WARN}")
        if self.elapsed > 0:
            time_str = f"{self.elapsed / 60:.1f}m" if self.elapsed >= 60 else f"{self.elapsed:.1f}s"
            mids[3] = _label(time_str, P.TEXT_DIM)
        if self.tool_count > 0:
            mids[4] = _label(f"{self.tool_count} 次工具", P.TEXT_DIM)

        avail = max(self.size.width - 2, 10)  # padding 0 1

        def assemble(keep: list) -> Text:
            parts = [brand]
            for item in keep:
                if item is not None:
                    parts += [sep, item]
            parts += [sep, status]
            return Text.assemble(*parts)

        keep = list(mids)
        full = assemble(keep)
        for idx in (4, 3, 1, 0):
            if full.cell_len <= avail:
                break
            keep[idx] = None
            full = assemble(keep)
        self.update(_content(full))


# ═══════════════════ 对话流 ═══════════════════


class ChatLog(ScrollableContainer):
    """对话流容器（贴底自动滚动）"""

    def _follow_tail(self):
        """贴底才自动跟随滚动（不打扰正在回看历史的用户）"""
        try:
            if self.max_scroll_y - self.scroll_y <= 3:
                self.scroll_end(animate=False)
        except Exception:
            pass

    def add_user_message(self, text: str):
        """添加用户消息"""
        timestamp = datetime.now().strftime("%H:%M")
        body = Text()
        body.append("你", style=f"bold {P.ACCENT_BRIGHT}")
        body.append(f"  {timestamp}\n", style=f"dim {P.TEXT_FAINT}")
        body.append(text, style=f"bold {P.TEXT}")
        widget = Static(_content(body), classes="msg msg-user")
        self.mount(widget)
        self.scroll_end(animate=False)

    def add_agent_container(self, agent_name: str) -> "AgentMessageContainer":
        """添加Agent消息容器（支持流式更新）"""
        container = AgentMessageContainer(agent_name)
        self.mount(container)
        self.scroll_end(animate=False)
        return container

    def add_system_message(self, text: str):
        """添加系统消息"""
        body = Text(text, style=f"dim {P.TEXT_DIM}")
        self.mount(Static(_content(body), classes="msg msg-system"))
        self.scroll_end(animate=False)

    def add_confirmation_buttons(self, buttons: list) -> "ConfirmationButtons":
        """添加确认按钮组（返回容器，供调用方在选择后禁用）"""
        container = ConfirmationButtons(buttons)
        self.mount(container)
        self.scroll_end(animate=False)
        return container

    def add_session_stats(self, stats: dict):
        """添加会话统计摘要"""
        body = Text()
        body.append("本轮分析", style=f"bold {P.ACCENT}")
        body.append("\n")
        if stats.get("rounds"):
            body.append(f"  推理轮次   {stats['rounds']}\n", style=P.TEXT_DIM)
        if stats.get("tool_count"):
            body.append(f"  累计工具   {stats['tool_count']} 次\n", style=P.TEXT_DIM)
        if stats.get("elapsed"):
            body.append(f"  总耗时     {stats['elapsed']:.1f}s\n", style=P.TEXT_DIM)
        if stats.get("tools_used"):
            body.append(f"  使用工具   {', '.join(stats['tools_used'])}\n", style=f"dim {P.TEXT_FAINT}")
        self.mount(Static(_content(body), classes="msg msg-stats"))
        self.scroll_end(animate=False)

    def clear(self):
        """清除所有消息"""
        self.remove_children()


# ═══════════════════ Agent 消息容器 ═══════════════════


class AgentMessageContainer(Static):
    """Agent消息容器（支持流式更新、thinking折叠）"""

    def __init__(self, agent_name: str):
        super().__init__(classes="msg msg-agent")
        self.agent_name = agent_name
        self.thinking_text = ""
        self.content_text = ""
        self.tools = []
        self.status = ""
        self.complete = False
        self.start_time = time.time()
        self.thinking_collapsed = True
        self._last_render_time: float = 0.0
        self._pending_render: bool = False
        self.refresh_display()

    def update_thinking(self, text: str):
        self.thinking_text = text
        self.refresh_display()

    def update_content(self, text: str):
        self.content_text = text
        self.refresh_display()

    def add_tool_badge(self, tool_name: str):
        self.tools.append(tool_name)
        self.refresh_display()

    def update_status(self, status: str):
        self.status = status
        self.refresh_display()

    def mark_complete(self):
        self.complete = True
        self._last_render_time = 0  # 重置节流，确保最终渲染立即执行
        self.refresh_display()

    def refresh_display(self):
        now = time.time()

        # 流式输出期间节流：未完成且距上次渲染不足 200ms 时跳过
        if not self.complete and self._last_render_time > 0:
            if now - self._last_render_time < _RENDER_THROTTLE:
                self._pending_render = True
                return

        self._last_render_time = now
        self._pending_render = False

        body = Text()
        elapsed = now - self.start_time

        # 标题行：Agent 名 + 状态 + 轮次工具等元信息
        body.append(self.agent_name, style=f"bold {P.ACCENT}")
        meta = []
        if self.status:
            meta.append(self.status)
        if elapsed > 1:
            meta.append(f"{elapsed:.1f}s")
        if meta:
            body.append("  " + " · ".join(meta), style=f"dim {P.TEXT_FAINT}")
        body.append("\n")

        # 思考过程（折叠/展开，点击切换）
        if self.thinking_text and len(self.thinking_text) > 20:
            if self.thinking_collapsed:
                preview = self.thinking_text[:40].replace("\n", " ")
                body.append(f"▸ 思考过程 {preview}…  [点击展开]", style=f"dim {P.TEXT_FAINT}")
            else:
                display_thinking = self.thinking_text
                if len(display_thinking) > 2000:
                    display_thinking = display_thinking[:1200] + "\n…（省略）…\n" + display_thinking[-600:]
                body.append("▾ 思考过程  [点击收起]", style=f"dim {P.TEXT_FAINT}")
                body.append("\n")
                body.append(display_thinking, style=f"dim {P.TEXT_FAINT}")
            body.append("\n")

        # 工具调用徽章
        if self.tools:
            chips = "  ".join(self.tools[-6:])
            body.append(f"⚙ {chips}", style=f"dim {P.WARN}")
            body.append("\n")

        # 输出内容（Markdown 已在写入时转为 Text，渲染期只换行）
        if self.content_text:
            body.append(markdown_to_text(self.content_text))

        # 状态指示
        if not self.complete:
            if self.content_text:
                body.append("▍", style=f"bold {P.ACCENT}")
            else:
                body.append("思考中…", style=f"dim {P.TEXT_FAINT}")

        self.update(_content(body))

        # 流式期间仅贴底跟随
        if not self.complete and isinstance(self.parent, ChatLog):
            self.parent._follow_tail()

    def on_click(self):
        """点击切换 thinking 折叠/展开"""
        if self.thinking_text and len(self.thinking_text) > 20:
            self.thinking_collapsed = not self.thinking_collapsed
            self.refresh_display()


# ═══════════════════ 工具面板 ═══════════════════


class ToolEntry(Static):
    """工具执行条目：点击展开/收起完整结果"""

    def __init__(self, entry: dict):
        super().__init__()
        self.entry = entry
        self.expanded = bool(entry.get("_expanded"))
        self.refresh_display()

    def refresh_display(self):
        e = self.entry
        body = Text()
        if e.get("running"):
            body.append("⚙ ", style=P.WARN)
            body.append(e["name"], style=P.WARN)
            hint = e.get("args_hint") or ""
            if hint:
                body.append(f"\n    {hint}", style=f"dim {P.TEXT_FAINT}")
        else:
            arrow = "▾" if self.expanded else "▸"
            body.append(f"{arrow} ", style=P.ACCENT)
            body.append(e["name"], style=P.TEXT)
            body.append(f"  {e.get('duration', 0):.1f}s", style=f"dim {P.TEXT_FAINT}")
            if self.expanded:
                for line in (e.get("result") or "（无结果）").splitlines():
                    body.append(f"\n    {line}", style=f"dim {P.TEXT_DIM}")
                body.append("\n    [点击收起]", style=f"dim {P.TEXT_FAINT}")
            else:
                hint = e.get("result_hint") or ""
                if hint:
                    body.append(f"\n    {hint}", style=f"dim {P.TEXT_FAINT}")
                body.append("  [展开]", style=f"dim {P.ACCENT}")
        self.update(_content(body))

    def on_click(self):
        if self.entry.get("running"):
            return
        self.expanded = not self.expanded
        self.entry["_expanded"] = self.expanded
        self.refresh_display()


class ToolPanel(Container):
    """工具执行面板（条目点击可展开完整结果）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executing: list[dict] = []
        self.completed: list[dict] = []
        self.border_title = "工具"

    def on_mount(self):
        self.refresh_display()

    def add_tool_execution(self, tool_name: str, args: dict):
        self.executing.append(
            {
                "name": tool_name,
                "args": args,
                "args_hint": self._args_hint(args or {}),
                "start": time.time(),
                "running": True,
            }
        )
        self.refresh_display()

    def complete_tool_execution(self, result: str):
        if self.executing:
            tool = self.executing.pop(0)
            tool["duration"] = time.time() - tool["start"]
            tool["result"] = (result or "")[:2000]
            tool["result_hint"] = self._result_hint(result or "")
            tool["running"] = False
            self.completed.append(tool)
            if len(self.completed) > 20:
                self.completed = self.completed[-20:]
            self.refresh_display()

    def clear_history(self):
        self.executing.clear()
        self.completed.clear()
        self.refresh_display()

    @staticmethod
    def _args_hint(args: dict) -> str:
        if not args:
            return ""
        items = list(args.items())[:3]
        return ", ".join(f"{k}={v}" for k, v in items)

    @staticmethod
    def _result_hint(result: str) -> str:
        """结果摘要：JSON 退化为 key=value 串，去掉括号引号，便于扫读"""
        s = " ".join((result or "").split())
        if s[:1] in ("{", "["):
            s = s.strip("{}[] ")
            s = s.replace('": "', "=").replace('":', "=").replace('"', "")
            s = s.replace(": ", "=").replace(":", "=").replace("= ", "=")
        return s[:44]

    def refresh_display(self):
        if not self.is_mounted:
            return
        self.remove_children()
        if not self.executing and not self.completed:
            self.mount(Static(_content(Text("  空闲", style=f"dim {P.TEXT_FAINT}"))))
            return
        if self.executing:
            self.mount(Static(_content(Text("执行中", style=f"bold {P.WARN}"))))
            for entry in self.executing[-3:]:
                self.mount(ToolEntry(entry))
        if self.completed:
            if self.executing:
                self.mount(Static(_content(Text(""))))
            self.mount(Static(_content(Text("已完成", style=f"bold {P.DOWN}"))))
            for entry in self.completed[-6:]:
                self.mount(ToolEntry(entry))


# ═══════════════════ 数据面板 ═══════════════════


class DataPanel(Static):
    """数据摘要面板"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data = {}
        self.refresh_display()

    def update_data(self, key: str, value: str):
        self.data[key] = value
        self.refresh_display()

    def clear_data(self):
        self.data = {}
        self.refresh_display()

    def refresh_display(self):
        body = Text()
        if self.data:
            first = True
            for key, value in self.data.items():
                if not first:
                    body.append("\n")
                first = False
                style = P.TEXT
                if any(k in key for k in ("涨", "涨幅", "涨跌")):
                    try:
                        v = float(str(value).strip("%"))
                        style = P.UP if v > 0 else P.DOWN if v < 0 else P.TEXT
                    except (ValueError, TypeError):
                        pass
                body.append(f"  {_pad_label(key)}", style=f"dim {P.TEXT_DIM}")
                body.append(str(value), style=f"bold {style}")
        else:
            body.append("  暂无数据", style=f"dim {P.TEXT_FAINT}")
        self.update(_content(body))
        self.border_title = "数据"


# ═══════════════════ 自选股面板 ═══════════════════


def _change_style(change: str) -> tuple[str, str]:
    """涨跌 → (样式颜色, 箭头)"""
    try:
        v = float(str(change).strip("%"))
        if v > 0:
            return P.UP, "▲"
        if v < 0:
            return P.DOWN, "▼"
    except (ValueError, TypeError):
        pass
    return P.TEXT_DIM, "●"


class WatchlistPanel(Static):
    """自选股面板"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stocks = []
        self.refresh_display()

    def set_stocks(self, stocks: list):
        self.stocks = stocks
        self.refresh_display()

    def refresh_display(self):
        body = Text()
        if self.stocks:
            shown = self.stocks[:6]
            for i, stock in enumerate(shown):
                code = stock.get("code", "")
                name = stock.get("name", "")
                price = stock.get("price", "")
                change = stock.get("change", "--")
                style, symbol = _change_style(change)

                if i:
                    body.append("\n")
                label = f"{name} · {code}" if name and name != code else code
                body.append(f"  {label}\n", style=P.TEXT)
                body.append(f"  {price}  {symbol} {change}", style=f"bold {style}")
            if len(self.stocks) > 6:
                body.append(f"\n  … 还有 {len(self.stocks) - 6} 只", style=f"dim {P.TEXT_FAINT}")
        else:
            body.append("  暂无自选股\n", style=f"dim {P.TEXT_FAINT}")
            body.append("  /watchlist add 600519", style=f"dim {P.TEXT_FAINT}")
        self.update(_content(body))
        self.border_title = "自选股"


# ═══════════════════ 持仓面板 ═══════════════════


class PortfolioPanel(Static):
    """持仓面板"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.positions = []
        self.refresh_display()

    def set_positions(self, positions: list):
        self.positions = positions
        self.refresh_display()

    def refresh_display(self):
        body = Text()
        if self.positions:
            shown = self.positions[:4]
            for i, pos in enumerate(shown):
                code = pos.get("code", "")
                name = pos.get("name", "")
                shares = pos.get("shares", 0)
                profit = pos.get("profit", 0)
                profit_pct = pos.get("profit_pct", 0)

                style = P.UP if profit > 0 else P.DOWN if profit < 0 else P.TEXT_DIM
                symbol = "▲" if profit > 0 else "▼" if profit < 0 else "●"

                if i:
                    body.append("\n")
                label = f"{name} · {code}" if name and name != code else code
                body.append(f"  {label}  {shares}股\n", style=P.TEXT)
                body.append(f"  {profit:+,.0f}元  {symbol} {profit_pct:+.1f}%", style=f"bold {style}")
            if len(self.positions) > 4:
                body.append(f"\n  … 还有 {len(self.positions) - 4} 只", style=f"dim {P.TEXT_FAINT}")
        else:
            body.append("  暂无持仓", style=f"dim {P.TEXT_FAINT}")
        self.update(_content(body))
        self.border_title = "持仓"


# ═══════════════════ 市场面板 ═══════════════════


class MarketPanel(Static):
    """市场概况面板"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.market_data = {}
        self.refresh_display()

    def update_market(self, data: dict):
        self.market_data = data
        self.refresh_display()

    def refresh_display(self):
        body = Text()
        if self.market_data:
            order = ["sh000001", "sz399001", "sz399006"]
            first = True
            for key in order:
                idx = self.market_data.get(key)
                if not idx:
                    continue
                name = idx.get("name", "")
                price = idx.get("price", 0)
                pct = idx.get("change_pct", 0)

                style, arrow = _change_style(f"{pct:+.2f}")
                if not first:
                    body.append("\n\n")
                first = False
                body.append(f"  {name}\n", style=P.TEXT)
                body.append(f"  {price:.2f}  {arrow} {pct:+.2f}%", style=f"bold {style}")
        else:
            body.append("  加载中…", style=f"dim {P.TEXT_FAINT}")
        self.update(_content(body))
        self.border_title = "市场"


# ═══════════════════ 确认按钮 ═══════════════════


class ConfirmationButtons(Container):
    """确认按钮组"""

    def __init__(self, buttons: list):
        super().__init__()
        self.button_configs = buttons

    def compose(self):
        for label, action in self.button_configs:
            btn = Button(label, id=f"confirm-{action}", variant="primary" if action == "accept" else "default")
            yield btn

    def disable_buttons(self):
        """选择后禁用全部按钮，防止重复提交"""
        for btn in self.query(Button):
            btn.disabled = True
