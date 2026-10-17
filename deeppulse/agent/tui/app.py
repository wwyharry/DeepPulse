"""DeepPulse TUI主应用 - 三栏布局 · 暗色金融主题"""

import asyncio
import json
import re
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Footer, Input

from deeppulse.agent.agent import StockAgent
from deeppulse.agent.judge_agent import JudgeAgent
from deeppulse.agent.tui.widgets import (
    ChatLog,
    DataPanel,
    MarketPanel,
    PortfolioPanel,
    StatusBar,
    ToolPanel,
    WatchlistPanel,
)

# 命令帮助文本
COMMAND_HELP = """
可用命令

  /help              显示此帮助
  /fetch [子命令]     数据采集 (stocks/kline/all/update/status，无需预先下载数据)
  /chart <代码> [天数] 生成K线图并打开（缺省代码取会话最近讨论的股票）
  /memory [子命令]    记忆管理 (list/search/predictions/profile)
  /strategy [关键词]  战法库搜索
  /watchlist          查看自选股
  /portfolio          查看持仓
  /judge              手动触发评判Agent
  /stats              显示会话统计
  /stop               停止当前分析
  /clear              清屏
  reset               重置对话
  quit / exit / q     退出

快捷键

  Ctrl+Q  退出        Ctrl+R  重置对话
  Ctrl+L  清屏        Ctrl+H  帮助
  Ctrl+J  手动评判    Ctrl+S  停止分析
  Ctrl+↑/↓ 历史命令   Tab  命令补全
"""

# Tab 补全候选（与 handle_command 分发保持一致）
COMMANDS = [
    "/help",
    "/fetch",
    "/chart",
    "/memory",
    "/strategy",
    "/watchlist",
    "/portfolio",
    "/judge",
    "/stats",
    "/stop",
    "/clear",
]

# 输入历史持久化文件（~/.deeppulse/tui_history.json）
HISTORY_FILE = Path.home() / ".deeppulse" / "tui_history.json"
HISTORY_MAX = 200


def load_history_file(path: Path) -> list[str]:
    """读取输入历史（文件缺失/损坏返回空）"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data][-HISTORY_MAX:]
    except Exception:
        pass
    return []


def save_history_file(path: Path, items: list[str]) -> None:
    """写入输入历史（尽力而为，失败不影响主流程）"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(items[-HISTORY_MAX:], ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


def extract_data_fields(text: str) -> dict:
    """从工具结果提取关键字段（容忍截断 JSON：解析失败时退化为正则抽取）"""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    out = {}
    for key in ("price", "change_pct", "rsi"):
        m = re.search(rf'"{key}"\s*:\s*"?(-?\d+(?:\.\d+)?)', text or "")
        if m:
            out[key] = m.group(1)
    return out


def fetch_needs_worker(sub: str) -> bool:
    """/fetch 子命令是否应放后台 worker（help/status 为秒回读操作）"""
    return sub not in ("", "help", "帮助", "status")


def parse_chart_args(arg: str, session_stocks: list[str] | None = None) -> tuple[str, int] | None:
    """解析 /chart 参数 → (代码, 天数)；无法确定代码时返回 None"""
    parts = (arg or "").split()
    code = parts[0] if parts else ""
    if not code:
        for s in session_stocks or []:
            if s.isdigit() and len(s) == 6:
                code = s
                break
    if not code:
        return None
    days = 120
    if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0:
        days = int(parts[1])
    return code, days

# 响应式断点（列）：窗口过窄时自动收起侧栏/收紧留白
# 对应 styles.tcss 中 Screen.hide-left / hide-right / compact
BP_HIDE_LEFT = 100
BP_HIDE_RIGHT = 78
BP_COMPACT = 58


class DeepPulseTUI(App):
    """DeepPulse TUI主应用"""

    TITLE = "DeepPulse - AI股票分析助手"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", key_display="^Q"),
        Binding("ctrl+r", "reset", "重置对话", key_display="^R"),
        Binding("ctrl+l", "clear_screen", "清屏", key_display="^L"),
        Binding("ctrl+h", "help", "帮助", key_display="^H"),
        Binding("ctrl+j", "judge", "评判", key_display="^J"),
        Binding("ctrl+s", "stop", "停止", key_display="^S"),
        Binding("ctrl+up", "history_prev", "上一条", show=False),
        Binding("ctrl+down", "history_next", "下一条", show=False),
        Binding("tab", "complete_input", show=False),
    ]

    def __init__(self, setting: dict = None, verbose: bool = False):
        super().__init__()
        self.setting = setting
        self.verbose = verbose
        self.agent = None
        self.judge_agent = None
        self.judge_result = None
        self.current_query = ""
        self._start_time = 0.0
        self._command_history: list[str] = []
        self._history_index = -1
        self._init_done = False

        # 运行态：busy-guard + 可取消的当前分析任务
        self._busy = False
        self._active_task: asyncio.Task | None = None
        self._session_saved = False
        self._history_file = HISTORY_FILE
        self._judge_history_dir = None  # 测试可注入；None=默认目录
        agent_cfg = (setting or {}).get("agent", {}) if isinstance(setting, dict) else {}
        self._judge_enabled = bool(agent_cfg.get("enable_judge", False))

        # 后台任务管理（防止 GC 回收 + 异常捕获）
        self._background_tasks: set[asyncio.Task] = set()

        # 面板错误计数（连续失败 N 次后显示提示）
        self._panel_errors: dict[str, int] = {"watchlist": 0, "portfolio": 0, "market": 0}

    def _create_task(self, coro, name: str = ""):
        """创建后台任务并保存引用，防止 GC 回收；异常时显示到 ChatLog"""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task):
        """后台任务完成回调：清理引用 + 捕获异常"""
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc and self.verbose:
            task_name = task.get_name() or "unknown"
            self.chat_log.add_system_message(f"⚠️ 后台任务 [{task_name}] 异常: {exc}")

    @staticmethod
    def _version() -> str:
        """包版本（找不到发行元数据时退回占位符）"""
        try:
            from importlib.metadata import version

            return "v" + version("deeppulse")
        except Exception:
            return ""

    def _apply_responsive(self, width: int) -> None:
        """按窗口宽度切换 Screen 类（TCSS 依类显隐侧栏/收紧留白）"""
        wanted = {
            "hide-left": width < BP_HIDE_LEFT,
            "hide-right": width < BP_HIDE_RIGHT,
            "compact": width < BP_COMPACT,
        }
        if getattr(self, "_resp_classes", None) == wanted:
            return  # 断点未跨越：不触发 CSS 重算
        self._resp_classes = wanted
        try:
            screen = self.screen
        except Exception:
            return
        for name, on in wanted.items():
            screen.set_class(on, name)

    async def on_resize(self, event) -> None:
        """窗口尺寸变化：重设响应式断点（布局重排由 Textual 核心处理）"""
        self._apply_responsive(event.size.width)

    def compose(self) -> ComposeResult:
        """构建三栏布局"""
        yield StatusBar(id="status-bar")

        with Horizontal(id="main-container"):
            # 左栏：自选股 + 持仓 + 市场
            with ScrollableContainer(id="left-panel"):
                yield WatchlistPanel(id="watchlist-panel")
                yield PortfolioPanel(id="portfolio-panel")
                yield MarketPanel(id="market-panel")

            # 中栏：对话流
            with Vertical(id="chat-panel"):
                yield ChatLog(id="chat-log")
                with Horizontal(id="input-row"):
                    yield Input(id="chat-input", placeholder="输入问题或 /help 查看命令... (Ctrl+Q 退出)")
                    yield Button("停止", id="stop-btn", variant="error")

            # 右栏：工具 + 数据
            with ScrollableContainer(id="right-panel"):
                yield ToolPanel(id="tool-panel")
                yield DataPanel(id="data-panel")

        yield Footer()

    async def on_mount(self) -> None:
        """启动时初始化"""
        self.chat_log = self.query_one("#chat-log", ChatLog)
        self.chat_input = self.query_one("#chat-input", Input)
        self.tool_panel = self.query_one("#tool-panel", ToolPanel)
        self.data_panel = self.query_one("#data-panel", DataPanel)
        self.status_bar = self.query_one("#status-bar", StatusBar)
        self.watchlist_panel = self.query_one("#watchlist-panel", WatchlistPanel)
        self.portfolio_panel = self.query_one("#portfolio-panel", PortfolioPanel)
        self.market_panel = self.query_one("#market-panel", MarketPanel)

        # 按初始窗口宽度设置响应式布局
        self._apply_responsive(self.size.width)

        # 恢复输入历史 + 初始运行态
        self._command_history = load_history_file(self._history_file)
        self._history_index = len(self._command_history)
        self._set_busy(False)

        # 显示欢迎消息
        self.chat_log.add_system_message(
            f"欢迎使用 DeepPulse {self._version()}！\n"
            "正在后台初始化 Agent，请稍候…\n"
            "输入 /help 查看所有命令和快捷键"
        )

        self.chat_input.focus()
        self._create_task(self._init_agents_async(), "init_agents")

    async def _init_agents_async(self):
        """异步初始化Agent（带重试）"""
        max_retries = 3
        retry_delays = [3, 6, 12]  # 指数退避秒数

        for attempt in range(max_retries):
            self.status_bar.set_status(f"初始化中{'...' if attempt == 0 else f' (重试 {attempt}/{max_retries - 1})'}")
            try:
                loop = asyncio.get_running_loop()

                self.agent = await loop.run_in_executor(
                    None, lambda: StockAgent(setting=self.setting, verbose=self.verbose)
                )

                if self._judge_enabled:
                    self.judge_agent = await loop.run_in_executor(None, lambda: JudgeAgent(setting=self.setting))

                # 更新状态栏
                model = (self.setting or {}).get("llm", {}).get("model", "unknown")
                self.status_bar.set_model(model)
                self.status_bar.set_session(self.agent.session_id)
                self.status_bar.set_status("就绪")

                self._init_done = True

                self.chat_log.add_system_message(
                    "✅ Agent 初始化完成！现在可以开始提问。"
                    + ("\n💡 按 Ctrl+J 或输入 /judge 让评判Agent检查分析质量。" if self._judge_enabled else "")
                )

                # 加载面板数据并启动定时刷新
                self._create_task(self._load_watchlist(), "init_watchlist")
                self._create_task(self._load_portfolio(), "init_portfolio")
                self._create_task(self._load_market(), "init_market")
                self.set_interval(60, self._refresh_watchlist)
                self.set_interval(60, self._refresh_portfolio)
                self.set_interval(300, self._refresh_market)
                return  # 成功，退出

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    self.chat_log.add_system_message(
                        f"⚠️ Agent 初始化失败 ({attempt + 1}/{max_retries}): {str(e)}\n⏳ {delay}秒后自动重试..."
                    )
                    await asyncio.sleep(delay)
                else:
                    self.chat_log.add_system_message(
                        f"❌ Agent 初始化失败（已重试 {max_retries} 次）: {str(e)}\n"
                        f"💡 请检查网络连接和 setting.json 配置，然后重启程序。"
                    )
                    self.status_bar.set_status("初始化失败")

    def _refresh_watchlist(self):
        """定时刷新自选股（由 set_interval 回调）"""
        self._create_task(self._load_watchlist(), "refresh_watchlist")

    async def _load_watchlist(self):
        """后台加载自选股实时数据"""
        try:
            from deeppulse import config as _config
            from deeppulse.agent.watchlist import WatchlistManager
            from deeppulse.src.realtime.manager import RealtimeQuoteManager

            loop = asyncio.get_running_loop()

            # 获取自选股代码列表（线程池执行，不阻塞UI）
            wl = WatchlistManager()
            codes = await loop.run_in_executor(None, wl.get_codes)
            if not codes:
                return

            # 批量获取实时行情
            manager = RealtimeQuoteManager(
                priority=_config.REALTIME_SOURCES,
                timeout=_config.REALTIME_TIMEOUT,
            )
            quotes = await loop.run_in_executor(None, manager.fetch_quotes, codes)

            # 组装面板数据
            stocks = []
            for code in codes:
                q = quotes.get(code)
                if q:
                    stocks.append(
                        {
                            "code": code,
                            "name": q.name or code,
                            "price": f"{q.current:.2f}" if q.current else "--",
                            "change": f"{q.change_pct:+.2f}%" if q.change_pct is not None else "--",
                        }
                    )
                else:
                    stocks.append({"code": code, "name": code, "price": "--", "change": "--"})

            if stocks:
                self.watchlist_panel.set_stocks(stocks)
            self._panel_errors["watchlist"] = 0  # 成功，重置计数
        except Exception as e:
            self._panel_errors["watchlist"] += 1
            if self._panel_errors["watchlist"] >= 3 and self.verbose:
                self.chat_log.add_system_message(f"⚠️ 自选股数据加载连续失败: {e}")

    def _refresh_portfolio(self):
        """定时刷新持仓"""
        self._create_task(self._load_portfolio(), "refresh_portfolio")

    async def _load_portfolio(self):
        """后台加载持仓实时数据"""
        try:
            from deeppulse import config as _config
            from deeppulse.agent.journal import TradeJournal
            from deeppulse.src.realtime.manager import RealtimeQuoteManager

            loop = asyncio.get_running_loop()

            journal = TradeJournal()
            portfolio = await loop.run_in_executor(None, journal.get_portfolio)
            if not portfolio:
                return

            codes = [p["code"] for p in portfolio]
            manager = RealtimeQuoteManager(
                priority=_config.REALTIME_SOURCES,
                timeout=_config.REALTIME_TIMEOUT,
            )
            quotes = await loop.run_in_executor(None, manager.fetch_quotes, codes)

            positions = []
            for p in portfolio:
                code = p["code"]
                name = p.get("name", "") or code
                shares = p.get("shares", 0)
                avg_cost = p.get("avg_cost", 0)

                q = quotes.get(code)
                current = q.current if q and q.current else 0

                cost = avg_cost * shares
                value = current * shares
                pnl = value - cost
                pnl_pct = (current - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0

                positions.append(
                    {
                        "code": code,
                        "name": name,
                        "shares": shares,
                        "profit": pnl,
                        "profit_pct": pnl_pct,
                    }
                )

            if positions:
                self.portfolio_panel.set_positions(positions)
            self._panel_errors["portfolio"] = 0  # 成功，重置计数
        except Exception as e:
            self._panel_errors["portfolio"] += 1
            if self._panel_errors["portfolio"] >= 3 and self.verbose:
                self.chat_log.add_system_message(f"⚠️ 持仓数据加载连续失败: {e}")

    def _refresh_market(self):
        """定时刷新市场概况"""
        self._create_task(self._load_market(), "refresh_market")

    async def _load_market(self):
        """后台加载三大指数（新浪实时）"""
        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, self._fetch_indices)
            if data:
                self.market_panel.update_market(data)
            self._panel_errors["market"] = 0  # 成功，重置计数
        except Exception as e:
            self._panel_errors["market"] += 1
            if self._panel_errors["market"] >= 3 and self.verbose:
                self.chat_log.add_system_message(f"⚠️ 市场数据加载连续失败: {e}")

    @staticmethod
    def _fetch_indices() -> dict | None:
        """通过新浪 API 获取上证/深证/创业板三大指数"""
        try:
            from curl_cffi import requests as curl_requests

            codes = ["sh000001", "sz399001", "sz399006"]
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            headers = {"Referer": "https://finance.sina.com.cn"}

            session = curl_requests.Session(impersonate="chrome")
            resp = session.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace").strip()

            indices = {}
            labels = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}

            for line in text.strip().split("\n"):
                line = line.strip()
                if not line or '=""' in line:
                    continue
                try:
                    var_part = line.split("=")[0]
                    sina_code = var_part.split("_")[-1]
                    data_str = line.split('"')[1]
                    fields = data_str.split(",")

                    name = labels.get(sina_code, fields[1])
                    current = float(fields[3]) if fields[3] else 0
                    yesterday = float(fields[2]) if fields[2] else 0

                    if yesterday > 0:
                        change_amt = round(current - yesterday, 2)
                        change_pct = round((current - yesterday) / yesterday * 100, 2)
                    else:
                        change_amt = 0
                        change_pct = 0

                    indices[sina_code] = {
                        "name": name,
                        "price": current,
                        "change_amt": change_amt,
                        "change_pct": change_pct,
                    }
                except (IndexError, ValueError):
                    continue

            return indices if indices else None
        except Exception:
            return None

    # ═══════════════════ 输入处理 ═══════════════════

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """处理用户输入"""
        if event.input.id != "chat-input":
            return

        query = event.value.strip()
        if not query:
            return

        event.input.value = ""
        if not self._command_history or self._command_history[-1] != query:
            self._command_history.append(query)
        self._history_index = len(self._command_history)
        save_history_file(self._history_file, self._command_history)

        # 特殊命令
        if query in ("quit", "exit", "q"):
            await self.action_quit()
            return
        if query == "reset":
            await self.action_reset()
            return
        if query.startswith("/"):
            await self.handle_command(query)
            return

        # 检查Agent
        if not self._init_done:
            self.chat_log.add_system_message("⏳ Agent 正在初始化中，请稍候...")
            return

        if self._busy:
            self.chat_log.add_system_message("⚠️ 当前分析仍在进行中（Ctrl+S 或 /stop 可停止）")
            self.chat_input.focus()
            return

        self.current_query = query
        self.chat_log.add_user_message(query)

        # 评判Agent 仅手动触发（/judge 或 Ctrl+J），用户输入一律走主分析
        self._spawn_agent(self.run_main_agent(query), "main_agent")

    async def action_history_prev(self):
        """上一条历史命令"""
        if not self._command_history:
            return
        if self._history_index > 0:
            self._history_index -= 1
        self.chat_input.value = self._command_history[self._history_index]

    async def action_history_next(self):
        """下一条历史命令"""
        if not self._command_history:
            return
        if self._history_index < len(self._command_history) - 1:
            self._history_index += 1
            self.chat_input.value = self._command_history[self._history_index]
        else:
            self._history_index = len(self._command_history)
            self.chat_input.value = ""

    # ═══════════════════ 运行态（busy-guard + 停止） ═══════════════════

    def _set_busy(self, busy: bool) -> None:
        """切换运行态：停止按钮显隐 + 输入框占位提示"""
        self._busy = busy
        try:
            btn = self.query_one("#stop-btn", Button)
            btn.set_class(busy, "-visible")
            btn.disabled = not busy
            self.chat_input.placeholder = (
                "分析中…（Ctrl+S 或 /stop 停止）" if busy else "输入问题或 /help 查看命令... (Ctrl+Q 退出)"
            )
        except Exception:
            pass

    def _spawn_agent(self, coro, name: str) -> asyncio.Task:
        """把 Agent 运行放到独立任务里，便于 /stop 取消"""
        self._set_busy(True)
        task = self._create_task(coro, name)
        self._active_task = task
        task.add_done_callback(self._on_agent_done)
        return task

    def _on_agent_done(self, task: asyncio.Task) -> None:
        if self._active_task is task:
            self._active_task = None
        self._set_busy(False)

    async def action_stop(self) -> None:
        """停止当前分析（取消后台任务）"""
        task = self._active_task
        if task is None or task.done():
            self.chat_log.add_system_message("⚠️ 当前没有正在进行的分析")
            self.chat_input.focus()
            return
        task.cancel()

    async def action_complete_input(self) -> None:
        """Tab：斜杠命令补全；否则照常切换焦点"""
        value = self.chat_input.value
        if self.chat_input.has_focus and value.startswith("/") and " " not in value:
            matches = [c for c in COMMANDS if c.startswith(value)]
            if len(matches) == 1:
                self.chat_input.value = matches[0] + " "
                self.chat_input.cursor_position = len(self.chat_input.value)
                return
            if len(matches) > 1:
                self.chat_log.add_system_message("可选命令: " + "  ".join(matches))
                return
        self.action_focus_next()

    # ═══════════════════ 会话保存 / 退出 ═══════════════════

    def _save_session(self) -> None:
        """尽力保存会话摘要与输入历史（幂等）"""
        if self._session_saved:
            return
        self._session_saved = True
        save_history_file(self._history_file, self._command_history)
        if self.agent:
            try:
                self.agent.on_session_end()
            except Exception:
                pass

    async def action_quit(self) -> None:
        """退出前保存会话摘要"""
        self._save_session()
        self.exit()

    async def on_unmount(self) -> None:
        """兜底：非 Ctrl+Q 路径退出（如终端关闭信号）也保存"""
        self._save_session()

    # ═══════════════════ 命令系统 ═══════════════════

    async def handle_command(self, cmd: str):
        """处理斜杠命令"""
        parts = cmd.strip().split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command == "/help":
            self.chat_log.add_system_message(COMMAND_HELP)

        elif command == "/clear":
            await self.action_clear_screen()

        elif command == "/judge":
            if not self._judge_enabled:
                self.chat_log.add_system_message("⚠️ 评判Agent 已禁用（默认关闭，用 --judge 启动或配置 setting.agent.enable_judge=true 开启）")
            elif self._busy:
                self.chat_log.add_system_message("⚠️ 当前分析仍在进行中（Ctrl+S 或 /stop 可停止）")
            elif self.agent and len(self.agent.messages) > 2:
                self._spawn_agent(self.run_judge_agent(), "judge_agent")
            else:
                self.chat_log.add_system_message("⚠️ 没有可以评判的内容，请先进行分析")

        elif command == "/stats":
            await self._show_stats()

        elif command == "/memory":
            await self._handle_memory_command(arg)

        elif command in ("/strategy", "/战法"):
            await self._handle_strategy_command(arg)

        elif command in ("/watchlist", "/自选"):
            await self._handle_watchlist_command(arg)

        elif command in ("/portfolio", "/持仓"):
            await self._handle_portfolio_command(arg)

        elif command == "/fetch":
            self._handle_fetch_command(arg)

        elif command == "/chart":
            self._handle_chart_command(arg)

        elif command == "/stop":
            await self.action_stop()

        else:
            self.chat_log.add_system_message(f"❓ 未知命令: {command}\n输入 /help 查看可用命令")

        self.chat_input.focus()

    def _handle_fetch_command(self, arg: str):
        """处理 /fetch：help/status 秒回同步执行，其余全部后台 worker（不卡 UI）"""
        from deeppulse.agent.fetch_commands import run_fetch_command

        sub = (arg or "").split()[:1]
        sub = sub[0].lower() if sub else ""

        if not fetch_needs_worker(sub):
            self.chat_log.add_system_message(f"▶️ /fetch {arg or 'help'}")
            result = run_fetch_command(arg, log=lambda m: self.chat_log.add_system_message(f"  {m}"))
            self.chat_log.add_system_message(result)
            return

        self.chat_log.add_system_message(f"▶️ /fetch {arg}（后台运行，期间可继续对话）")

        def work():
            result = run_fetch_command(
                arg, log=lambda m: self.call_from_thread(self.chat_log.add_system_message, f"  {m}")
            )
            self.call_from_thread(self.chat_log.add_system_message, result)

        self.run_worker(work, thread=True, name="fetch", group="fetch")

    def _handle_chart_command(self, arg: str):
        """处理 /chart：后台生成K线图并自动打开"""
        session_stocks = list(self.agent._session_stocks) if self.agent else []
        parsed = parse_chart_args(arg, session_stocks)
        if not parsed:
            self.chat_log.add_system_message("用法: /chart <代码> [天数]，如 /chart 600519 120")
            return
        code, days = parsed
        self.chat_log.add_system_message(f"▶️ 生成 {code} K线图（{days} 个交易日）…")

        def work():
            from deeppulse.agent.charts import generate_kline_chart

            path = generate_kline_chart(code, days=days, auto_open=True)
            self.call_from_thread(self.chat_log.add_system_message, f"📈 K线图已生成: {path}")

        self.run_worker(work, thread=True, name="chart", group="chart")

    async def _show_stats(self):
        """显示会话统计"""
        if not self.agent:
            self.chat_log.add_system_message("⚠️ Agent 未初始化")
            return

        msg_count = len(self.agent.messages)
        tool_count = sum(1 for m in self.agent.messages if m.get("role") == "tool")
        session_id = self.agent.session_id[:8]
        compaction = getattr(self.agent, "_compaction_stats", {}) or {}
        folded_msgs = compaction.get("folded_messages", 0)
        folded_calls = compaction.get("folded_tool_calls", 0)

        stats_text = (
            f"📊 会话统计\n\n"
            f"  会话ID: {session_id}\n"
            f"  消息总数: {msg_count}\n"
            f"  工具调用: {tool_count} 次\n"
            f"  历史折叠: {folded_msgs} 条（含 {folded_calls} 次工具调用）\n"
            f"  跟踪股票: {', '.join(self.agent._session_stocks) or '无'}\n"
            f"  讨论主题: {', '.join(self.agent._session_topics) or '无'}"
        )
        self.chat_log.add_system_message(stats_text)

    async def _handle_memory_command(self, arg: str):
        """处理 /memory 命令"""
        if not self.agent:
            self.chat_log.add_system_message("⚠️ Agent 未初始化")
            return

        try:
            parts = arg.strip().split(maxsplit=1) if arg else [""]
            sub = parts[0]
            sub_arg = parts[1] if len(parts) > 1 else ""

            if sub in ("", "list"):
                result = self.agent.memory.list_memories(limit=15)
                data = json.loads(result)
                if not data.get("results"):
                    self.chat_log.add_system_message("🧠 暂无长期记忆")
                else:
                    lines = [f"🧠 共 {data['total']} 条记忆:\n"]
                    for m in data["results"]:
                        lines.append(f"  [{m['memory_type']}] {m['content'][:80]}...")
                    self.chat_log.add_system_message("\n".join(lines))

            elif sub == "search":
                if not sub_arg:
                    self.chat_log.add_system_message("用法: /memory search <关键词>")
                    return
                result = self.agent.memory.search_memories(sub_arg)
                data = json.loads(result)
                if not data.get("results"):
                    self.chat_log.add_system_message(f"🔍 未找到与「{sub_arg}」相关的记忆")
                else:
                    lines = [f"🔍 找到 {data['total']} 条相关记忆:\n"]
                    for m in data["results"]:
                        score = m.get("score", 0)
                        lines.append(f"  [{m['memory_type']}] {m['content'][:60]}... (相关度: {score:.2f})")
                    self.chat_log.add_system_message("\n".join(lines))

            elif sub == "predictions":
                result = self.agent.memory.prediction_tracker.get_accuracy_stats()
                data = json.loads(result)
                self.chat_log.add_system_message(
                    f"📈 预测准确率:\n"
                    f"  总验证: {data.get('total_verified', 0)} | "
                    f"正确: {data.get('correct', 0)} | "
                    f"准确率: {data.get('accuracy', 'N/A')}"
                )

            elif sub == "profile":
                result = self.agent.memory.user_profile.get_profile()
                data = json.loads(result)
                profile = data.get("profile", {})
                if not profile:
                    self.chat_log.add_system_message("👤 暂无用户画像数据")
                else:
                    lines = ["👤 用户交易画像:\n"]
                    for key, info in profile.items():
                        conf = info.get("confidence", 0)
                        lines.append(f"  {key}: {info['value']} (置信度: {conf:.0%})")
                    self.chat_log.add_system_message("\n".join(lines))

            else:
                self.chat_log.add_system_message("可用子命令: list, search, predictions, profile")

        except Exception as e:
            self.chat_log.add_system_message(f"⚠️ 记忆命令执行失败: {str(e)}")

    async def _handle_strategy_command(self, arg: str):
        """处理 /strategy 命令"""
        try:
            from deeppulse.agent.strategy_loader import get_strategy_loader

            loader = get_strategy_loader()

            if arg.startswith("search "):
                query = arg[7:]
                results = loader.search(query)
                if not results:
                    self.chat_log.add_system_message(f"📚 未找到匹配「{query}」的战法")
                else:
                    lines = [f"📚 找到 {len(results)} 个匹配战法:\n"]
                    for r in results:
                        lines.append(f"  [{r['score']:.1f}] {r['name']} ({r['file']})")
                    self.chat_log.add_system_message("\n".join(lines))
            elif arg:
                content = loader.get_strategy_content(arg)
                if content:
                    # 截取前 500 字符
                    display = content[:500] + "..." if len(content) > 500 else content
                    self.chat_log.add_system_message(f"📜 {arg}:\n{display}")
                else:
                    self.chat_log.add_system_message(f"📚 未找到战法: {arg}")
            else:
                strategies = loader.list_strategies()
                if not strategies:
                    self.chat_log.add_system_message("📚 暂无战法。请将 .md 文件放入 deeppulse/strategy/strategies/")
                else:
                    lines = [f"📚 共 {len(strategies)} 个战法:\n"]
                    for s in strategies[:15]:
                        tags = ", ".join(s["tags"][:2]) if s["tags"] else ""
                        lines.append(f"  {s['name']} [{tags}]")
                    self.chat_log.add_system_message("\n".join(lines))
        except Exception as e:
            self.chat_log.add_system_message(f"⚠️ 战法命令执行失败: {str(e)}")

    async def _handle_watchlist_command(self, arg: str):
        """处理 /watchlist 命令"""
        try:
            from deeppulse.agent.watchlist import WatchlistManager, format_watchlist_status

            wl = WatchlistManager()

            # 同步刷新面板
            self._create_task(self._load_watchlist(), "cmd_watchlist")

            if not arg or arg == "list":
                result = format_watchlist_status(wl)
                self.chat_log.add_system_message(f"📊 自选股:\n{result}")
            elif arg.startswith("add "):
                code = arg[4:].strip().split()[0]
                result = wl.add(code)
                if result.get("status") == "ok":
                    self.chat_log.add_system_message(f"✅ 已添加 {result.get('name', code)}({result.get('code', code)})")
                else:
                    self.chat_log.add_system_message(f"❌ 添加失败: {result.get('message', result)}")
            elif arg.startswith("remove "):
                code = arg[7:].strip()
                result = wl.remove(code)
                if result.get("status") == "ok":
                    self.chat_log.add_system_message(f"✅ 已移除 {result.get('name', code)}({result.get('code', code)})")
                else:
                    self.chat_log.add_system_message(f"❌ 移除失败: {result.get('message', result)}")
            else:
                self.chat_log.add_system_message("用法: /watchlist [list|add <代码>|remove <代码>]")
        except Exception as e:
            self.chat_log.add_system_message(f"⚠️ 自选股命令执行失败: {str(e)}")

    async def _handle_portfolio_command(self, arg: str):
        """处理 /portfolio 命令"""
        try:
            from deeppulse.agent.journal import TradeJournal, format_portfolio_status, format_trade_history

            j = TradeJournal()

            # 同步刷新面板
            self._create_task(self._load_portfolio(), "cmd_portfolio")

            if not arg or arg == "list":
                result = format_portfolio_status(j)
                self.chat_log.add_system_message(f"💼 持仓:\n{result}")
            elif arg == "history":
                result = format_trade_history(j, days=14)
                self.chat_log.add_system_message(f"📜 交易记录:\n{result}")
            elif arg == "review":
                from deeppulse.agent.journal import generate_auto_review

                result = generate_auto_review(j, "week")
                self.chat_log.add_system_message(f"📊 周度复盘:\n{result}")
            else:
                self.chat_log.add_system_message("用法: /portfolio [list|history|review]")
        except Exception as e:
            self.chat_log.add_system_message(f"⚠️ 持仓命令执行失败: {str(e)}")

    # ═══════════════════ Agent 执行 ═══════════════════

    async def run_main_agent(self, query: str):
        """运行主分析Agent"""
        main_container = self.chat_log.add_agent_container("主分析")
        self._start_time = time.time()

        thinking_buffer = ""
        content_buffer = ""
        current_round = 0

        self.status_bar.set_status("分析中...")

        try:
            async for event_type, text in self.agent.chat_stream_async(query):
                if event_type == "round":
                    current_round = int(text)
                    main_container.update_status(f"Round {text}")
                    self.status_bar.update_round(current_round)

                elif event_type == "thinking":
                    thinking_buffer += text
                    main_container.update_thinking(thinking_buffer)

                elif event_type == "content":
                    content_buffer += text
                    main_container.update_content(content_buffer)

                elif event_type == "tool_start":
                    tool_name, tool_args = self.parse_tool_call(text)
                    self.tool_panel.add_tool_execution(tool_name, tool_args)
                    main_container.add_tool_badge(tool_name)
                    self.status_bar.increment_tool_count()

                    if tool_name == "realtime_price":
                        self.data_panel.update_data("查询", tool_args.get("code", ""))
                    # 写操作落地后立即刷新面板（tool_start 在工具执行完毕后才下发），
                    # 不等 60s 定时器——否则用户看不到增删自选/持仓生效
                    if tool_name in ("add_to_watchlist", "remove_from_watchlist", "set_alert_rule"):
                        self._create_task(self._load_watchlist(), "refresh_watchlist")
                    elif tool_name in ("record_trade", "save_review"):
                        self._create_task(self._load_portfolio(), "refresh_portfolio")

                elif event_type == "tool_result":
                    self.tool_panel.complete_tool_execution(text)
                    result = extract_data_fields(text)
                    if "price" in result:
                        self.data_panel.update_data("当前价", str(result["price"]))
                    if "change_pct" in result:
                        self.data_panel.update_data("涨跌幅", str(result["change_pct"]))
                    if "rsi" in result:
                        self.data_panel.update_data("RSI", str(result["rsi"]))

                elif event_type == "done":
                    main_container.mark_complete()
                    break

            elapsed = time.time() - self._start_time
            self.status_bar.update_elapsed(elapsed)
            self.status_bar.set_status("就绪")

            # 显示会话统计
            tools_used = list({m.get("name", "") for m in self.agent.messages if m.get("role") == "tool"})
            self.chat_log.add_session_stats(
                {
                    "rounds": current_round,
                    "tool_count": self.status_bar.tool_count,
                    "elapsed": elapsed,
                    "tools_used": tools_used,
                }
            )

        except asyncio.CancelledError:
            main_container.update_content("\n\n⏹ 已停止")
            main_container.mark_complete()
            self.status_bar.set_status("就绪")
            raise
        except Exception as e:
            main_container.update_content(f"❌ 错误: {str(e)}")
            main_container.mark_complete()
            self.status_bar.set_status("就绪")

    async def run_judge_agent(self):
        """运行评判Agent（全流程评测）"""
        if not self.agent or not self.agent.messages or len(self.agent.messages) < 3:
            self.chat_log.add_system_message("⚠️ 对话内容不足，无法进行评测")
            return

        judge_container = self.chat_log.add_agent_container("评判")
        judge_container.update_status("全流程评测中...")
        self.status_bar.set_status("评判中...")

        content_buffer = ""
        issues = []
        score = None
        summary = None

        try:
            async for event_type, text in self.judge_agent.judge_stream_async(self.agent.messages):
                if event_type == "content":
                    content_buffer += text
                    judge_container.update_content(content_buffer)

                elif event_type == "issues_found":
                    data = json.loads(text)
                    issue_count = data.get("count", 0)
                    judge_container.update_status(f"发现 {issue_count} 个问题")

                elif event_type == "score":
                    score = text
                    judge_container.update_status(f"评分: {score}/10")

                elif event_type == "summary":
                    # 消费结构化摘要（评分/类型/时间戳），供认可后落盘报告
                    try:
                        summary = json.loads(text)
                    except Exception:
                        summary = None

                elif event_type == "done":
                    judge_container.mark_complete()
                    break

        except asyncio.CancelledError:
            judge_container.update_content("\n\n⏹ 已停止")
            judge_container.mark_complete()
            self.status_bar.set_status("就绪")
            raise
        except Exception as e:
            judge_container.update_content(f"❌ 评判出错: {str(e)}")
            judge_container.mark_complete()

        self.judge_result = {"content": content_buffer, "issues": issues, "score": score, "summary": summary}
        self.status_bar.set_status("就绪")
        self.show_confirmation_dialog()

    def show_confirmation_dialog(self):
        self._confirm_buttons = self.chat_log.add_confirmation_buttons([("认可", "accept"), ("不认可", "reject")])

    async def on_button_pressed(self, event) -> None:
        button_id = event.button.id
        if button_id == "stop-btn":
            await self.action_stop()
            return
        if button_id == "confirm-accept":
            await self.handle_accept()
        elif button_id == "confirm-reject":
            self.chat_log.add_system_message("✅ 已忽略此次评判")
        else:
            return
        # 选择后禁用按钮，防止重复提交
        confirm = getattr(self, "_confirm_buttons", None)
        if confirm is not None:
            confirm.disable_buttons()
        self.chat_input.focus()

    async def handle_accept(self):
        if not self.judge_result or not self.judge_result.get("content"):
            self.chat_log.add_system_message("⚠️ 没有可记录的评判内容")
            self.chat_input.focus()
            return

        # 落盘评测报告（正文 + 结构化摘要），供 /portfolio review 等复盘引用
        try:
            from deeppulse.agent.judge_agent import JudgeHistory

            summary = self.judge_result.get("summary") or {"score": self.judge_result.get("score")}
            filepath = JudgeHistory(store_dir=self._judge_history_dir).save_evaluation(
                session_id=(self.agent.session_id if self.agent else "unknown"),
                summary=summary,
                report=self.judge_result.get("content", ""),
            )
            self.chat_log.add_system_message(f"✅ 评测报告已保存: {filepath}")
        except Exception as e:
            self.chat_log.add_system_message(f"⚠️ 评测报告保存失败: {str(e)}")

        try:
            from deeppulse.agent.tools import TOOL_DISPATCH

            score = self.judge_result.get("score")
            score_note = f"评分 {score}/10" if score is not None else "未给出评分"
            TOOL_DISPATCH["record_learning"](
                learned_what=f"用户认可评判Agent对「{self.current_query[:50]}...」的检查意见（{score_note}）",
                learned_why="评判Agent发现了主分析中的潜在问题或改进点",
                apply_when="在进行类似分析时参考这些检查点",
                category="user_correction",
                importance=0.7,
            )
            self.chat_log.add_system_message("✅ 已记录评判内容到学习记忆")
        except Exception as e:
            self.chat_log.add_system_message(f"⚠️ 记录失败: {str(e)}")

        self.chat_input.focus()

    def parse_tool_call(self, text: str) -> tuple:
        try:
            idx = text.index("(")
            tool_name = text[:idx]
            args_str = text[idx + 1 : -1]
            tool_args = json.loads(args_str) if args_str else {}
            return tool_name, tool_args
        except Exception:
            return text, {}

    # ═══════════════════ 快捷键动作 ═══════════════════

    async def action_reset(self) -> None:
        if self.agent:
            # 重置前保存上一轮会话摘要（历史不丢失）
            self._save_session()
            self._session_saved = False  # 新会话可再保存
            self.agent.reset()
        self.chat_log.clear()
        self.data_panel.clear_data()
        self.tool_panel.clear_history()
        self.status_bar.reset_session_stats()
        self.chat_log.add_system_message("🔄 对话已重置")
        self.chat_input.focus()

    async def action_clear_screen(self) -> None:
        self.chat_log.clear()
        self.chat_input.focus()

    async def action_help(self) -> None:
        self.chat_log.add_system_message(COMMAND_HELP)
        self.chat_input.focus()

    async def action_judge(self) -> None:
        if not self._judge_enabled:
            self.chat_log.add_system_message("⚠️ 评判Agent 已禁用（默认关闭，用 --judge 启动或配置 setting.agent.enable_judge=true 开启）")
            self.chat_input.focus()
        elif self._busy:
            self.chat_log.add_system_message("⚠️ 当前分析仍在进行中（Ctrl+S 或 /stop 可停止）")
            self.chat_input.focus()
        elif self.agent and len(self.agent.messages) > 2:
            self._spawn_agent(self.run_judge_agent(), "judge_agent")
        else:
            self.chat_log.add_system_message("⚠️ 没有可以评判的内容，请先进行分析")
            self.chat_input.focus()


def run_tui(setting: dict = None, verbose: bool = False):
    """启动TUI应用"""
    app = DeepPulseTUI(setting=setting, verbose=verbose)
    app.run()
