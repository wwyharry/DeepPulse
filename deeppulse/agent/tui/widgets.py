"""TUI界面核心组件 - 暗色金融主题"""

import time
from datetime import datetime

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.containers import Container, ScrollableContainer
from textual.widgets import Button, Static

# Markdown 渲染节流间隔（秒），防止流式输出时 O(n²) 重渲染
_RENDER_THROTTLE = 0.2

# ═══════════════════ 状态栏 ═══════════════════


class StatusBar(Static):
    """顶部状态栏：模型 | 会话 | 轮次 | 耗时 | 工具数"""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        dock: top;
        width: 100%;
        background: #161b22;
        color: #8b949e;
        padding: 0 1;
    }
    """

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

    def refresh_display(self):
        parts = []
        parts.append(Text("⚡ DeepPulse", style="bold #58a6ff"))
        parts.append(Text(" │ ", style="#30363d"))

        if self.model_name:
            short_model = self.model_name.split("/")[-1] if "/" in self.model_name else self.model_name
            parts.append(Text(f"模型: {short_model}", style="#8b949e"))
            parts.append(Text(" │ ", style="#30363d"))

        if self.session_id:
            parts.append(Text(f"会话: {self.session_id}", style="#8b949e"))
            parts.append(Text(" │ ", style="#30363d"))

        if self.round_num > 0:
            parts.append(Text(f"R{self.round_num}", style="#d29922"))
            parts.append(Text(" │ ", style="#30363d"))

        if self.elapsed > 0:
            if self.elapsed >= 60:
                time_str = f"{self.elapsed / 60:.1f}m"
            else:
                time_str = f"{self.elapsed:.1f}s"
            parts.append(Text(f"⏱ {time_str}", style="#8b949e"))
            parts.append(Text(" │ ", style="#30363d"))

        if self.tool_count > 0:
            parts.append(Text(f"🔧 {self.tool_count}", style="#8b949e"))
            parts.append(Text(" │ ", style="#30363d"))

        parts.append(Text(self.status_text, style="#3fb950" if self.status_text == "就绪" else "#d29922"))

        self.update(Group(*parts))


# ═══════════════════ 对话流 ═══════════════════


class ChatLog(ScrollableContainer):
    """对话流容器（自动滚动）"""

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        border: solid #30363d;
        background: #0d1117;
        padding: 1;
        scrollbar-color: #30363d;
        scrollbar-color-hover: #484f58;
        scrollbar-color-active: #6e7681;
        scrollbar-gutter: stable;
    }
    """

    def add_user_message(self, text: str):
        """添加用户消息"""
        timestamp = datetime.now().strftime("%H:%M")
        widget = Static()
        widget.update(
            Panel(
                Markdown(text),
                title=f"👤 你  {timestamp}",
                border_style="#58a6ff",
                padding=(0, 1),
            )
        )
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
        widget = Static()
        widget.update(Panel(Text(text, style="#8b949e"), border_style="#30363d", padding=(0, 1)))
        self.mount(widget)
        self.scroll_end(animate=False)

    def add_confirmation_buttons(self, buttons: list):
        """添加确认按钮组"""
        button_group = ConfirmationButtons(buttons)
        self.mount(button_group)
        self.scroll_end(animate=False)

    def add_session_stats(self, stats: dict):
        """添加会话统计摘要"""
        widget = Static()
        lines = []
        lines.append(Text("📊 本次分析统计", style="bold #58a6ff"))
        lines.append(Text(""))
        if stats.get("rounds"):
            lines.append(Text(f"  推理轮次: {stats['rounds']}", style="#c9d1d9"))
        if stats.get("tool_count"):
            lines.append(Text(f"  工具调用: {stats['tool_count']} 次", style="#c9d1d9"))
        if stats.get("elapsed"):
            lines.append(Text(f"  总耗时: {stats['elapsed']:.1f}s", style="#c9d1d9"))
        if stats.get("tools_used"):
            tools_str = ", ".join(stats["tools_used"])
            lines.append(Text(f"  使用工具: {tools_str}", style="#8b949e"))

        content = Group(*lines)
        widget.update(Panel(content, border_style="#30363d", padding=(0, 1)))
        self.mount(widget)
        self.scroll_end(animate=False)

    def clear(self):
        """清除所有消息"""
        self.remove_children()


# ═══════════════════ Agent 消息容器 ═══════════════════


class AgentMessageContainer(Static):
    """Agent消息容器（支持流式更新、thinking折叠）"""

    DEFAULT_CSS = """
    AgentMessageContainer {
        margin: 0 0 1 0;
    }
    """

    def __init__(self, agent_name: str):
        super().__init__()
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

        parts = []
        elapsed = now - self.start_time

        # 思考过程（折叠/展开）
        if self.thinking_text and len(self.thinking_text) > 20:
            if self.thinking_collapsed:
                preview = self.thinking_text[:100].replace("\n", " ")
                parts.append(Text(f"💭 {preview}...  [展开 ▼]", style="dim #6e7681"))
            else:
                # 截取显示
                display_thinking = self.thinking_text
                if len(display_thinking) > 2000:
                    display_thinking = display_thinking[:1200] + "\n...(省略)...\n" + display_thinking[-600:]
                parts.append(Text("💭 思考过程 [收起 ▲]", style="dim #6e7681"))
                parts.append(Text(display_thinking, style="dim #484f58"))

        # 工具调用徽章
        if self.tools:
            tool_badges = " ".join([f"[{t}]" for t in self.tools[-6:]])
            parts.append(Text(f"🔧 {tool_badges}", style="#d29922"))

        # 输出内容（Markdown渲染，带异常保护）
        if self.content_text:
            try:
                parts.append(Markdown(self.content_text))
            except Exception:
                # Markdown 解析失败（不完整语法等），fallback 到纯文本
                parts.append(Text(self.content_text, style="#c9d1d9"))

        # 状态指示
        if not self.complete:
            if self.content_text:
                parts.append(Text("▊", style="blink #58a6ff"))
            else:
                parts.append(Text("⏳ 思考中...", style="dim #6e7681"))

        content = Group(*parts) if parts else Text("⏳ 等待响应...", style="dim #6e7681")

        # 标题：Agent名 + 状态 + 耗时
        title_parts = [self.agent_name]
        if self.status:
            title_parts.append(self.status)
        if elapsed > 1:
            title_parts.append(f"{elapsed:.1f}s")
        title = " │ ".join(title_parts)

        border_color = "#3fb950" if self.complete else "#d29922"
        self.update(Panel(content, title=title, border_style=border_color, padding=(0, 1)))

        # 触发父容器滚动
        if hasattr(self.parent, "scroll_end"):
            self.parent.scroll_end(animate=False)

    def on_click(self):
        """点击切换 thinking 折叠/展开"""
        if self.thinking_text and len(self.thinking_text) > 20:
            self.thinking_collapsed = not self.thinking_collapsed
            self.refresh_display()


# ═══════════════════ 工具面板 ═══════════════════


class ToolPanel(Static):
    """工具执行面板"""

    DEFAULT_CSS = """
    ToolPanel {
        height: auto;
        border: solid #30363d;
        background: #0d1117;
        padding: 1;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executing = []
        self.completed = []
        self.refresh_display()

    def add_tool_execution(self, tool_name: str, args: dict):
        self.executing.append({"name": tool_name, "args": args, "start": time.time()})
        self.refresh_display()

    def complete_tool_execution(self, result: str):
        if self.executing:
            tool = self.executing.pop(0)
            tool["duration"] = time.time() - tool["start"]
            tool["result"] = result[:80]
            self.completed.append(tool)
            if len(self.completed) > 20:
                self.completed = self.completed[-20:]
            self.refresh_display()

    def clear_history(self):
        self.executing.clear()
        self.completed.clear()
        self.refresh_display()

    def refresh_display(self):
        lines = []

        # 正在执行
        if self.executing:
            lines.append(Text("🔄 执行中", style="bold #d29922"))
            for tool in self.executing:
                lines.append(Text(f"  ⏳ {tool['name']}", style="#d29922"))
        else:
            lines.append(Text("⏸ 空闲", style="dim #6e7681"))

        lines.append(Text(""))

        # 已完成（最近 6 个）
        if self.completed:
            lines.append(Text("✅ 已完成", style="bold #3fb950"))
            for tool in self.completed[-6:]:
                duration = tool.get("duration", 0)
                lines.append(Text(f"  ✓ {tool['name']}  {duration:.1f}s", style="#3fb950"))

        content = Group(*lines)
        self.update(Panel(content, title="🔧 工具面板", border_style="#30363d", padding=(0, 1)))


# ═══════════════════ 数据面板 ═══════════════════


class DataPanel(Static):
    """数据摘要面板"""

    DEFAULT_CSS = """
    DataPanel {
        height: auto;
        border: solid #30363d;
        background: #0d1117;
        padding: 1;
    }
    """

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
        lines = []
        if self.data:
            for key, value in self.data.items():
                # 涨跌颜色
                style = "#c9d1d9"
                if any(k in key for k in ("涨", "涨幅", "涨跌")):
                    try:
                        v = float(str(value).strip("%"))
                        style = "#f85149" if v > 0 else "#3fb950" if v < 0 else "#c9d1d9"
                    except (ValueError, TypeError):
                        pass
                lines.append(Text(f"  {key}: {value}", style=style))
        else:
            lines.append(Text("  暂无数据", style="dim #6e7681"))

        content = Group(*lines)
        self.update(Panel(content, title="📊 数据摘要", border_style="#30363d", padding=(0, 1)))


# ═══════════════════ 自选股面板 ═══════════════════


class WatchlistPanel(Static):
    """自选股面板"""

    DEFAULT_CSS = """
    WatchlistPanel {
        height: auto;
        border: solid #30363d;
        background: #0d1117;
        padding: 1;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stocks = []
        self.refresh_display()

    def set_stocks(self, stocks: list):
        self.stocks = stocks
        self.refresh_display()

    def refresh_display(self):
        lines = []
        if self.stocks:
            for stock in self.stocks[:6]:
                code = stock.get("code", "")
                name = stock.get("name", "")
                price = stock.get("price", "")
                change = stock.get("change", "0")

                try:
                    change_val = float(str(change).strip("%"))
                    if change_val > 0:
                        style = "#f85149"
                        symbol = "▲"
                    elif change_val < 0:
                        style = "#3fb950"
                        symbol = "▼"
                    else:
                        style = "#8b949e"
                        symbol = "●"
                except (ValueError, TypeError):
                    style = "#8b949e"
                    symbol = "●"

                label = f"{name}({code})" if name and name != code else code
                lines.append(Text(f" {symbol} {label}", style=style))
                lines.append(Text(f"   {price}  {change}", style=style))
        else:
            lines.append(Text(" 暂无自选股", style="dim #6e7681"))
            lines.append(Text(" 输入股票代码开始分析", style="dim #484f58"))

        content = Group(*lines)
        self.update(Panel(content, title="📊 自选股", border_style="#30363d", padding=(0, 1)))


# ═══════════════════ 持仓面板 ═══════════════════


class PortfolioPanel(Static):
    """持仓面板"""

    DEFAULT_CSS = """
    PortfolioPanel {
        height: auto;
        border: solid #30363d;
        background: #0d1117;
        padding: 1;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.positions = []
        self.refresh_display()

    def set_positions(self, positions: list):
        self.positions = positions
        self.refresh_display()

    def refresh_display(self):
        lines = []
        if self.positions:
            for pos in self.positions[:4]:
                code = pos.get("code", "")
                name = pos.get("name", "")
                shares = pos.get("shares", 0)
                profit = pos.get("profit", 0)
                profit_pct = pos.get("profit_pct", 0)

                style = "#f85149" if profit > 0 else "#3fb950" if profit < 0 else "#8b949e"
                symbol = "▲" if profit > 0 else "▼" if profit < 0 else "●"

                label = f"{name}({code})" if name and name != code else code
                lines.append(Text(f" {symbol} {label}  {shares}股", style="#c9d1d9"))
                lines.append(Text(f"   {profit:+.0f}元 ({profit_pct:+.1f}%)", style=style))
        else:
            lines.append(Text(" 暂无持仓", style="dim #6e7681"))

        content = Group(*lines)
        self.update(Panel(content, title="💼 持仓", border_style="#30363d", padding=(0, 1)))


# ═══════════════════ 市场面板 ═══════════════════


class MarketPanel(Static):
    """市场概况面板"""

    DEFAULT_CSS = """
    MarketPanel {
        height: auto;
        border: solid #30363d;
        background: #0d1117;
        padding: 1;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.market_data = {}
        self.refresh_display()

    def update_market(self, data: dict):
        self.market_data = data
        self.refresh_display()

    def refresh_display(self):
        lines = []
        if self.market_data:
            order = ["sh000001", "sz399001", "sz399006"]
            for key in order:
                idx = self.market_data.get(key)
                if not idx:
                    continue
                name = idx.get("name", "")
                price = idx.get("price", 0)
                pct = idx.get("change_pct", 0)
                amt = idx.get("change_amt", 0)

                if pct > 0:
                    style = "#f85149"
                    arrow = "▲"
                elif pct < 0:
                    style = "#3fb950"
                    arrow = "▼"
                else:
                    style = "#8b949e"
                    arrow = "●"

                lines.append(Text(f" {name}", style="bold #c9d1d9"))
                lines.append(Text(f"  {price:.2f}  {arrow}{pct:+.2f}%  ({amt:+.2f})", style=style))
        else:
            lines.append(Text(" 加载中...", style="dim #6e7681"))

        content = Group(*lines)
        self.update(Panel(content, title="📈 市场概况", border_style="#30363d", padding=(0, 1)))


# ═══════════════════ 确认按钮 ═══════════════════


class ConfirmationButtons(Container):
    """确认按钮组"""

    DEFAULT_CSS = """
    ConfirmationButtons {
        height: auto;
        layout: horizontal;
        align: center middle;
        padding: 0 0 1 0;
        background: #0d1117;
    }

    ConfirmationButtons Button {
        margin: 0 1;
        min-width: 14;
        height: 3;
    }
    """

    def __init__(self, buttons: list):
        super().__init__()
        self.button_configs = buttons

    def compose(self):
        for label, action in self.button_configs:
            btn = Button(label, id=f"confirm-{action}", variant="primary" if action == "accept" else "default")
            yield btn
