"""Web UI 测试假件：假 Agent / 假 curl / 假 LLMClient / SSE 解析 / create_app 注入。

全部离线：不构造真 StockAgent、不打真实网络、不落真实 ~/.deeppulse。
"""

import contextlib
import copy
import importlib
import json
import re
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

# ── 合法四段配置（llm/agent/embedding/data），key 为明文假 key 供掩码断言 ──
# api_key 尾4位 "abcd" → 掩码应为 "sk-***abcd"
FAKE_SETTING = {
    "llm": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-testkey0123456789abcd",
        "model": "deepseek-v4-pro",
        "protocol": "openai",
        "max_tokens": 2048,
        "temperature": 0.3,
        "timeout": 60,
    },
    "embedding": {
        "provider": "none",
        "model": "bge-small-zh",
        "api_key": "",
        "base_url": "",
    },
    "agent": {
        "max_rounds": 8,
        "verbose": False,
        "enable_judge": False,
    },
    "data": {
        "online_fallback": True,
        "cache_to_db": False,
        "default_source": "eastmoney",
        "db_path": None,
    },
}

# FakeWebAgent 初始 messages[0]：新 system（含可辨识标记，供「回灌换新 system」断言）
NEW_SYSTEM_TEXT = "新system-含时变上下文标记NEW"
# 回灌前落库的旧 system（陈旧不可回灌，断言它被替换掉）
STALE_SYSTEM_TEXT = "旧system-时变上下文已陈旧STALE"


def fake_setting() -> dict:
    """FAKE_SETTING 深拷贝（防测试间互相污染）"""
    return copy.deepcopy(FAKE_SETTING)


def import_create_app():
    """取 create_app（web.app 优先，兼容从 web 包顶层导出）"""
    fn = None
    for name in ("web.app", "web"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        try:
            fn = getattr(mod, "create_app", None)
        except ImportError:
            fn = None
        if fn is not None:
            return fn
    raise ImportError("找不到 create_app（web.app 尚未就位或未导出 create_app）")


# ── SSE 帧解析 ──────────────────────────────────────────────────────


def parse_sse(text: str) -> list:
    """把 SSE 响应体解析为 [{"event": str, "data": obj|None}, ...]"""
    frames = []
    for block in re.split(r"\r?\n\r?\n", text.strip() or ""):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            line = line.rstrip("\r")
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                raw = line[len("data:") :].strip()
                data = json.loads(raw) if raw else None
        if event is not None:
            frames.append({"event": event, "data": data})
    return frames


def frame_events(frames: list) -> list:
    return [f["event"] for f in frames]


# ── 假 Agent ───────────────────────────────────────────────────────


class _AgentBase:
    """假 Agent 公共骨架：实例登记 + 收尾调用计数 + 最小 agent-like 接口"""

    instances: list = []

    def __init__(self, session_id=None, protocol="openai", **_kwargs):
        self.session_id = session_id or "fake-session-0000"
        # LLM 客户端鸭子属性（protocol 供落盘/回灌协议闸门判定，见 restore_agent_state）
        self.client = SimpleNamespace(protocol=protocol)
        self.messages = [{"role": "system", "content": NEW_SYSTEM_TEXT}]
        self._user_messages = []
        self._session_stocks = set()
        self._session_topics = set()
        self.on_session_end_calls = 0
        self.on_session_end_forget = None  # 记录 forget 语义（删除会话必须 forget=True）
        self.reset_calls = 0
        self.queries = []
        self.cancelled = False
        self.chat_finished = False
        self.started = threading.Event()  # 首帧产出时置位，供并发/停止测试同步
        type(self).instances.append(self)

    def on_session_end(self, forget: bool = False):
        self.on_session_end_calls += 1
        self.on_session_end_forget = forget

    def reset(self):
        self.reset_calls += 1
        self.messages = [{"role": "system", "content": NEW_SYSTEM_TEXT}]
        self._user_messages = []
        self._session_stocks = set()
        self._session_topics = set()

    def _begin_turn(self, q):
        self.queries.append(q)
        self._user_messages.append(q)
        self.messages.append({"role": "user", "content": q})
        self._session_stocks.add("600519")
        self._session_topics.add("白酒")

    @staticmethod
    def _assistant_message() -> dict:
        """openai 形 assistant 消息：tool_calls.function.arguments 是 JSON 字符串（落库不二次解码）"""
        return {
            "role": "assistant",
            "content": "分析完成：趋势震荡。",
            "tool_calls": [
                {
                    "id": "call_fake_1",
                    "type": "function",
                    "function": {"name": "get_kline", "arguments": '{"code": "600519"}'},
                }
            ],
        }


DEFAULT_EVENTS = [
    ("round", 1),
    ("thinking", "先看基本面再看技术面"),
    ("content", "分析完成：当前趋势震荡，支撑位参考 1700。"),
    ("tool_start", 'get_kline({"code": "600519"})'),
    ("tool_result", '{"price": 1856.0, "change_pct": 1.23, "rsi": 32.1}'),
    ("done", ""),
]


class FakeWebAgent(_AgentBase):
    """可配事件序列的 async 生成器假 Agent（可直接当 agent_factory 用）"""

    default_events: list = DEFAULT_EVENTS

    def __init__(self, session_id=None, events=None, messages=None, **kwargs):
        super().__init__(session_id, **kwargs)
        self.events = copy.deepcopy(self.default_events if events is None else events)
        if messages is not None:
            self.messages = copy.deepcopy(messages)

    async def chat_stream_async(self, q):
        self._begin_turn(q)
        for ev_type, text in self.events:
            self.started.set()
            yield ev_type, text
        self.messages.append(self._assistant_message())
        self.chat_finished = True


class HangingAgent(_AgentBase):
    """挂在事件上等待取消：首帧后永久挂起，被 cancel 时记录 cancelled"""

    async def chat_stream_async(self, q):
        self._begin_turn(q)
        self.started.set()
        yield "thinking", "开始分析…"
        try:
            await threading_to_asyncio_event()  # 永不 set，等待取消
        except BaseException:
            self.cancelled = True
            raise


class ErrorAgent(_AgentBase):
    """中途抛异常：产出两帧后抛 RuntimeError，验证 error 帧不悬挂"""

    async def chat_stream_async(self, q):
        self._begin_turn(q)
        self.started.set()
        yield "thinking", "开始分析…"
        yield "content", "分析中"
        raise RuntimeError("模拟工具执行失败")


def threading_to_asyncio_event():
    """返回一个永不 set 的 asyncio.Event().wait() 协程（挂在事件上）"""
    import asyncio

    return asyncio.Event().wait()


def fake_agent_factory(**cfg):
    """构造 agent_factory：session_id -> FakeWebAgent(events=..., messages=...)"""
    events = cfg.pop("events", None)
    messages = cfg.pop("messages", None)

    def factory(session_id):
        return FakeWebAgent(session_id, events=events, messages=messages, **cfg)

    return factory


def clear_fake_agents():
    for cls in (FakeWebAgent, HangingAgent, ErrorAgent):
        cls.instances.clear()


def last_agent(cls=FakeWebAgent):
    return cls.instances[-1] if cls.instances else None


# ── TestClient 注入 ────────────────────────────────────────────────


@contextlib.contextmanager
def make_test_client(tmp_path, agent_factory=FakeWebAgent, setting=None, session_root=None, db_path=None):
    """create_app(注入契约) + TestClient，with 包裹以触发 lifespan"""
    from fastapi.testclient import TestClient

    create_app = import_create_app()
    clear_fake_agents()
    app = create_app(
        setting=fake_setting() if setting is None else setting,
        session_root=Path(session_root) if session_root is not None else Path(tmp_path) / "sessions",
        agent_factory=agent_factory,
        db_path=Path(db_path) if db_path is not None else Path(tmp_path) / "t.duckdb",
    )
    with TestClient(app) as client:
        yield client


def build_raw_app(tmp_path, agent_factory=FakeWebAgent, setting=None, session_root=None, db_path=None):
    """create_app(注入契约) 但不套 TestClient——供手工 ASGI 驱动（断连/取消路径测试）。

    调用方自行 `async with app.router.lifespan_context(app)` 进出 lifespan。
    """
    create_app = import_create_app()
    clear_fake_agents()
    return create_app(
        setting=fake_setting() if setting is None else setting,
        session_root=Path(session_root) if session_root is not None else Path(tmp_path) / "sessions",
        agent_factory=agent_factory,
        db_path=Path(db_path) if db_path is not None else Path(tmp_path) / "t.duckdb",
    )


async def asgi_request(app, method, path, payload=None, send_hook=None):
    """手工驱动一条 ASGI HTTP 请求，返回 (sent_messages, 异常或 None)。

    - send_hook(msg)：每次 send 之后调用，可抛 OSError 模拟发送中断连（C3）；
    - 返回的异常用于断言 ClientDisconnect / CancelledError 传播路径（C4）。
    """
    body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)
        if send_hook is not None:
            send_hook(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    exc = None
    try:
        await app(scope, receive, send)
    except BaseException as e:  # noqa: BLE001 —— 断连/取消路径就是要拿到这个异常
        exc = e
    return sent, exc


def sent_sse_frames(sent: list) -> list:
    """把 ASGI send 消息里的 SSE body 拼起来解析成帧列表"""
    text = "".join(m.get("body", b"").decode("utf-8", "replace") for m in sent if m.get("type") == "http.response.body")
    return parse_sse(text)


def sent_status(sent: list) -> int | None:
    """响应状态码（未发出 response.start 则 None）"""
    for m in sent:
        if m.get("type") == "http.response.start":
            return int(m.get("status") or 0)
    return None


# ── 会话落盘文件（session_store JSON 形状） ─────────────────────────


def write_session_file(
    root,
    sid,
    messages=None,
    title="历史会话",
    created_at="2026-01-01T00:00:00",
    updated_at="2026-01-01T00:00:00",
    **extra,
):
    """直接写 {root}/{sid}.json，形状对齐契约 §2（供坏 JSON / cap / 回灌用例预置）"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    data = {
        "id": sid,
        "title": title,
        "created_at": created_at,
        "updated_at": updated_at,
        "protocol": "openai",
        "messages": messages if messages is not None else [],
        "user_messages": ["老问题"],
        "session_stocks": ["600519"],
        "session_topics": ["白酒"],
    }
    data.update(extra)
    path = root / f"{sid}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def read_session_file(root, sid) -> dict:
    return json.loads((Path(root) / f"{sid}.json").read_text(encoding="utf-8"))


def stored_history_messages() -> list:
    """回灌用例的预置 stored 消息：旧 system + user + assistant（arguments 为 JSON 字符串）"""
    return [
        {"role": "system", "content": STALE_SYSTEM_TEXT},
        {"role": "user", "content": "老问题：茅台现在能买吗"},
        {
            "role": "assistant",
            "content": "老回答：先看K线。",
            "tool_calls": [
                {
                    "id": "call_old_1",
                    "type": "function",
                    "function": {"name": "get_kline", "arguments": '{"code": "600519", "days": 60}'},
                }
            ],
        },
    ]


# ── 假 curl_cffi（新浪指数 HTTP 层） ────────────────────────────────


class FakeCurlResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


def sina_payload() -> bytes:
    """新浪行情三指数 GBK 报文：fields[0]=名称、fields[1]=开盘价、fields[2]=昨收、fields[3]=今收"""
    lines = [
        'var hq_str_sh000001="上证指数,3300.00,3200.00,3250.50,3260.00,3190.00";',
        'var hq_str_sz399001="深证成指,10000.00,9900.00,9950.25,9960.00,9850.00";',
        'var hq_str_sz399006="创业板指,2000.00,1900.00,1950.10,1960.00,1850.00";',
    ]
    return ("\n".join(lines) + "\n").encode("gbk")


def patch_sina(monkeypatch, payload=None):
    """把 curl_cffi.requests.Session 换成假会话；返回 calls 列表（供断言 Referer/URL）

    payload: bytes=正常报文（默认 sina_payload()）；Exception=请求直接抛出（错误分支）。
    """
    body = sina_payload() if payload is None else payload
    err = body if isinstance(body, Exception) else None
    raw = b"" if err is not None else bytes(body)
    calls: list = []

    class _FakeSession:
        def __init__(self, impersonate=None, **_kw):
            self.impersonate = impersonate

        def get(self, url, headers=None, timeout=None, **_kw):
            calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
            if err is not None:
                raise err
            return FakeCurlResponse(raw)

    import curl_cffi.requests as curl_requests

    monkeypatch.setattr(curl_requests, "Session", _FakeSession)
    # 兼容 `from curl_cffi.requests import Session` 的模块级绑定
    with contextlib.suppress(ImportError):
        indices_mod = importlib.import_module("web.service.indices")
        if hasattr(indices_mod, "Session"):
            monkeypatch.setattr(indices_mod, "Session", _FakeSession)
    return calls


# ── 假 RealtimeQuoteManager.fetch_quotes ───────────────────────────


def make_quote(code, name="贵州茅台", current=1856.0, yesterday=1833.4, change_pct=None):
    from deeppulse.src.realtime.base import RealtimeQuote

    change_amount = round(current - yesterday, 2) if current is not None else None
    if change_pct is None:
        change_pct = round((current - yesterday) / yesterday * 100, 2) if current is not None else None
    return RealtimeQuote(
        code=code,
        name=name,
        current=current,
        open=yesterday,
        high=current,
        low=yesterday,
        yesterday_close=yesterday,
        change_amount=change_amount,
        change_pct=change_pct,
        volume=12345678,  # 单位：股
        amount=2.3e9,
        trade_date="2026-10-17",
        trade_time="10:30:00",
        data_source="fake",
    )


def patch_fetch_quotes(monkeypatch, quotes: dict | None = None):
    """替换 RealtimeQuoteManager.fetch_quotes：缺码直接缺席字典（面板显示 '--'）"""
    table = dict(quotes or {})

    def fake_fetch_quotes(self, codes, **_kw):
        return {c: table[c] for c in codes if c in table}

    from deeppulse.src.realtime.manager import RealtimeQuoteManager

    monkeypatch.setattr(RealtimeQuoteManager, "fetch_quotes", fake_fetch_quotes)
    for mod_name in ("web.service.singletons", "web.api.watchlist", "web.api.portfolio", "web.api.market"):
        with contextlib.suppress(ImportError):
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "fetch_quotes"):
                monkeypatch.setattr(mod, "fetch_quotes", lambda codes, **kw: fake_fetch_quotes(None, codes))
    return table


def patch_market_overview(monkeypatch, payload):
    """替换 market_overview（返回 JSON 字符串，与 tools 层口径一致）"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def fake_market_overview():
        return text

    import deeppulse.agent.tools.market as tools_market

    monkeypatch.setattr(tools_market, "market_overview", fake_market_overview)
    for mod_name in ("web.api.market", "web.api.commands", "web.service.singletons"):
        with contextlib.suppress(ImportError):
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "market_overview"):
                monkeypatch.setattr(mod, "market_overview", fake_market_overview)
    return text


# ── 假 LLMClient（settings/test 探活） ─────────────────────────────


class FakeLLMClient:
    """鸭子类型 LLMClient：chat() 可配延迟或抛错；记录构造参数"""

    instances: list = []
    fail_message: str | None = None
    latency_s: float = 0.0

    def __init__(self, setting=None, **kwargs):
        self.setting = setting
        self.kwargs = kwargs
        self.chat_messages = None
        type(self).instances.append(self)

    def chat(self, messages, **_kwargs):
        self.chat_messages = messages
        if type(self).fail_message:
            raise RuntimeError(type(self).fail_message)
        if type(self).latency_s:
            time.sleep(type(self).latency_s)
        return "pong"

    @classmethod
    def reset(cls):
        cls.instances.clear()
        cls.fail_message = None
        cls.latency_s = 0.0


def patch_llmclient(monkeypatch, fake_cls=None):
    """把 LLMClient 换成假实现（deeppulse 源头 + web 各引用点）"""
    fake = fake_cls or FakeLLMClient
    client_mod = importlib.import_module("deeppulse.agent.client")
    monkeypatch.setattr(client_mod, "LLMClient", fake)
    for mod_name in (
        "web.app",
        "web.api.settings",
        "web.api.commands",
        "web.service.settings_store",
        "web.service.singletons",
        "web.service.agent_pool",
    ):
        with contextlib.suppress(ImportError):
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "LLMClient"):
                monkeypatch.setattr(mod, "LLMClient", fake)
    return fake


# ── setting.json 路径注入 + 磁盘配置读写 ────────────────────────────


def patch_setting_path(monkeypatch, path):
    """monkeypatch web.service.settings_store.SETTING_PATH"""
    mod = importlib.import_module("web.service.settings_store")
    monkeypatch.setattr(mod, "SETTING_PATH", Path(path))
    return Path(path)


def write_setting_file(path, setting: dict) -> Path:
    path = Path(path)
    path.write_text(json.dumps(setting, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_setting_file(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── stock_info 播种（歧义候选用例） ────────────────────────────────


def seed_stock_info(db_path, rows):
    import duckdb

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS stock_info (code VARCHAR, name VARCHAR)")
        for code, name in rows:
            conn.execute("INSERT INTO stock_info VALUES (?, ?)", [code, name])
    finally:
        conn.close()


# ── 看板事实源打桩（dashboard_service 的源函数均为调用时导入，patch 模块属性即生效） ──

_POOL_FNS = {
    "zt": "get_limit_up_pool",
    "dt": "get_limit_down_pool",
    "zb": "get_failed_limit_up",
}


def patch_pool(monkeypatch, which, payload=None, calls=None):
    """替换涨停/跌停/炸板池函数。

    payload: dict=直接返回（error 字典可模拟失败）；Exception=抛出；
    callable=以 trade_date 调用并返回（日期敏感用例，如回声仿真）。
    dict 正常池仅对今天返回，其余日期给空池——对齐东财真实语义（早于最后有池日的
    周末/节假日返回空而非回声），否则常量池会被回声检测判为「没有自己的池」一路回退。
    error 字典任何日期都返回（探测即失败，不回退）。
    calls: 可选列表，记录每次调用的 trade_date。
    """
    fn_name = _POOL_FNS[which]
    log = calls if calls is not None else []
    home = date.today().strftime("%Y%m%d")

    def fake(trade_date=None):
        log.append(trade_date)
        if callable(payload):
            return payload(trade_date)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            return {"date": trade_date, "count": 0, "stocks": []}
        if "error" in payload:
            return payload
        if trade_date in (None, home):
            return payload
        return {"date": trade_date, "count": 0, "stocks": []}

    import deeppulse.agent.market as agent_market

    monkeypatch.setattr(agent_market, fn_name, fake)
    return log


def patch_snapshot(monkeypatch, df=None, exc=None, calls=None):
    """替换 online.fetch_market_snapshot（df 直接返回；exc 抛出）"""
    log = calls if calls is not None else []

    def fake():
        log.append(1)
        if exc is not None:
            raise exc
        return df

    import deeppulse.src.online as online_mod

    monkeypatch.setattr(online_mod, "fetch_market_snapshot", fake)
    return log


def make_snap_df(rows):
    """构造东财口径全市场快照行 → DataFrame。

    rows: iterable of dict(code,name,price,change_pct,high,prev_close[,turnover,amount,volume])
    成交额单位元（与东财一致，推导层自行换算亿）；volume 单位手（与东财一致，仅作零成交判定）。
    """
    import pandas as pd

    records = []
    for r in rows:
        records.append(
            {
                "代码": str(r.get("code", "")).zfill(6),
                "名称": r.get("name", ""),
                "最新价": r.get("price"),
                "涨跌幅": r.get("change_pct"),
                "最高": r.get("high"),
                "昨收": r.get("prev_close"),
                "换手率": r.get("turnover"),
                "成交额": r.get("amount"),
                "成交量": r.get("volume"),
            }
        )
    df = pd.DataFrame.from_records(records)
    if df.empty:
        df = pd.DataFrame(columns=["代码", "名称", "最新价", "涨跌幅", "最高", "昨收", "换手率", "成交额", "成交量"])
    return df


def patch_news_sources(monkeypatch, em=None, sina=None, calls=None):
    """替换东财/新浪新闻函数。payload：list 正常 / [{"error":…}] 失败 / Exception 抛出"""
    log = calls if calls is not None else []

    def _make(name, payload):
        def fake(num=10, **_kw):
            log.append(name)
            if isinstance(payload, Exception):
                raise payload
            return payload if payload is not None else []

        return fake

    import deeppulse.agent.news as agent_news

    monkeypatch.setattr(agent_news, "search_eastmoney_news", _make("em", em))
    monkeypatch.setattr(agent_news, "search_sina_finance", _make("sina", sina))
    return log


def patch_sector_ranking(monkeypatch, payload=None, calls=None):
    """替换 get_sector_ranking（payload 可为 {"error":…} 或 Exception 模拟失败）"""
    log = calls if calls is not None else []

    def fake(board_type="industry", top_n=10):
        log.append(board_type)
        if isinstance(payload, Exception):
            raise payload
        return payload if payload is not None else {"type": "行业板块", "source": "同花顺详情", "sectors": []}

    import deeppulse.agent.market as agent_market

    monkeypatch.setattr(agent_market, "get_sector_ranking", fake)
    return log


def patch_indices_with_source(monkeypatch, items=None, source="sina", err=None, calls=None):
    """替换 web.service.indices.fetch_indices_with_source（看板指数事实源）"""
    log = calls if calls is not None else []

    def fake():
        log.append(1)
        return list(items or []), source, err

    import web.service.indices as indices_mod

    monkeypatch.setattr(indices_mod, "fetch_indices_with_source", fake)
    return log


def guard_sentiment(monkeypatch):
    """把 get_market_sentiment 换成必炸桩：看板若复用它（含评级/操作建议）立刻假红"""
    import deeppulse.agent.market as agent_market

    def boom(*_a, **_kw):
        raise AssertionError("看板禁止调用 get_market_sentiment（含情绪评级/操作建议）")

    monkeypatch.setattr(agent_market, "get_market_sentiment", boom)


# 看板禁荐股词表（键名全局扫；值扫描见测试，news 面板豁免——第三方标题/摘要不加工）
BAN_KEYS = (
    "advice",
    "操作建议",
    "情绪评级",
    "sentiment",
    "rating",
    "score",
    "评分",
    "评级",
    "recommend",
    "建议",
    "仓位",
    "目标价",
)
BAN_VALUES = (
    "操作建议",
    "情绪评级",
    "高潮期",
    "冰点期",
    "赚钱效应",
    "试错",
    "仓位",
    "评级",
    "评分",
    "推荐",
    "目标价",
    "逐步锁定",
    "积极参与",
    "空仓等待",
)


def assert_no_recommendation(obj, *, skip_value_paths=("panels.news",), _path=""):
    """递归断言看板响应无任何荐股语义：键名禁词全局扫，值禁词按路径豁免 news"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_path = f"{_path}.{k}" if _path else str(k)
            for ban in BAN_KEYS:
                assert ban not in str(k), f"禁词键名 {ban!r} 出现在 {key_path}"
            assert_no_recommendation(v, skip_value_paths=skip_value_paths, _path=key_path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_no_recommendation(v, skip_value_paths=skip_value_paths, _path=f"{_path}[{i}]")
    elif isinstance(obj, str):
        if any(_path.startswith(p) for p in skip_value_paths):
            return
        for ban in BAN_VALUES:
            assert ban not in obj, f"禁词值 {ban!r} 出现在 {_path}: {obj[:60]!r}"
