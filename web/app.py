"""FastAPI 应用装配 —— create_app 注入契约 + lifespan 单例 + 静态前端挂载"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.api import chat as chat_api
from web.api import commands as commands_api
from web.api import dashboard as dashboard_api
from web.api import market as market_api
from web.api import portfolio as portfolio_api
from web.api import sessions as sessions_api
from web.api import settings as settings_api
from web.api import watchlist as watchlist_api
from web.api.common import SafeJSONResponse, error_response
from web.service.agent_pool import AgentPool
from web.service.session_store import SessionStore
from web.service.settings_store import load_setting_file
from web.service.singletons import ServiceSingletons

# web/static 前端目录（impl:frontend 写 html/css/js，后端仅挂载）
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_SESSION_ROOT = Path.home() / ".deeppulse" / "web_sessions"

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class SameOriginGuard:
    """副作用接口同源校验（CSRF 防护）。

    浏览器跨站发起的 POST/PUT/PATCH/DELETE（含 text/plain 简单请求）带的 Origin
    必不等于本站 Host —— 一律 403。无 Origin 头（curl / 内部测试 / 同源兜底）放行。
    这挡住「恶意网页借本机服务把 api_key 发去攻击者 base_url / 烧 LLM 配额」的攻击面。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") in _MUTATING_METHODS:
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
            origin = headers.get("origin")
            if origin is not None:
                host = headers.get("host", "")
                netloc = urlsplit(origin).netloc
                if origin.strip().lower() == "null" or not host or not netloc or netloc.lower() != host.lower():
                    body = json.dumps({"detail": "跨站请求被拒绝（同源校验失败）"}, ensure_ascii=False).encode("utf-8")
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [
                                (b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode("latin1")),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
        await self.app(scope, receive, send)


class StaticCachePolicy:
    """静态前端资产缓存策略：Cache-Control: no-cache（每次协商新鲜度，命中 ETag 省流量）。

    无策略时浏览器启发式缓存会长期钉死旧版 app.css/index.html——主题/样式大改后
    用户「看不到新功能」。no-cache 只禁「直接用缓存不校验」，仍允许 304。
    仅作用于 `/`、`/index.html` 与 `/static/*`，API 响应语义不动。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope.get("type") == "http"
            and scope.get("method") in ("GET", "HEAD")
            and (path in ("/", "/index.html") or path.startswith("/static/"))
        ):

            async def send_with_policy(message):
                if message.get("type") == "http.response.start":
                    headers = list(message.get("headers") or [])
                    headers.append((b"cache-control", b"no-cache"))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_policy)
            return
        await self.app(scope, receive, send)


def create_app(
    setting: dict | None = None,
    *,
    session_root: Path | None = None,
    agent_factory=None,
    db_path: Path | None = None,
) -> FastAPI:
    """构造 FastAPI 应用。

    Args:
        setting: 内存配置；None = 从 setting.json 读（宽松，占位 key 不阻断启动）
        session_root: 会话存储目录；None = ~/.deeppulse/web_sessions
        agent_factory: session_id -> agent-like；None = 真 StockAgent
        db_path: DuckDB 路径注入（测试用 tmp 库，不落真实库）
    """
    cfg = setting if setting is not None else load_setting_file()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 单例懒建一次（构造即 DDL/熔断器，绝不每请求新建）
        singletons = ServiceSingletons(db_path=db_path, setting=cfg)
        await asyncio.to_thread(singletons.init)
        store = SessionStore(Path(session_root) if session_root else DEFAULT_SESSION_ROOT)
        pool = AgentPool(cfg, agent_factory, store)
        app.state.singletons = singletons
        app.state.session_store = store
        app.state.agent_pool = pool
        app.state.setting = cfg
        yield
        # 退出时对活跃会话做记忆收尾（超时兜底吞异常）
        for sid in list(pool._runtimes):
            rt = pool._runtimes.get(sid)
            if rt is None or rt.agent is None:
                continue
            try:
                await asyncio.wait_for(asyncio.to_thread(rt.agent.on_session_end), timeout=15.0)
            except Exception:
                pass

    # SafeJSONResponse：NaN/Inf → null 兜底（pandas NULL→NaN 曾把 watchlist 打 500）
    app = FastAPI(title="DeepPulse Web", lifespan=lifespan, default_response_class=SafeJSONResponse)
    # CSRF 同源校验先于一切路由（见 SameOriginGuard docstring）
    app.add_middleware(SameOriginGuard)
    # 静态资产防陈旧缓存（见 StaticCachePolicy docstring）
    app.add_middleware(StaticCachePolicy)

    @app.get("/api/health")
    async def health():
        llm = (app.state.setting or {}).get("llm") or {}
        model = llm.get("model") or ""
        # agent_ready：模型已配置且 api_key 非占位（构造 StockAgent 不会立刻 ValueError）
        from web.service.settings_store import _is_placeholder_api_key

        agent_ready = bool(model) and not _is_placeholder_api_key(llm.get("api_key"))
        return {"status": "ok", "model": model, "agent_ready": agent_ready}

    @app.exception_handler(404)
    async def _not_found(request, exc):
        return error_response("接口不存在", status_code=404)

    app.include_router(chat_api.router)
    app.include_router(sessions_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(market_api.router)
    app.include_router(watchlist_api.router)
    app.include_router(portfolio_api.router)
    app.include_router(settings_api.router)
    app.include_router(commands_api.router)

    # 静态前端最后挂载（不遮蔽 /api）
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    # index.html 引用 /static/* —— 必须显式挂载；根挂载只兜底 / 与 /<name>
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static-files")
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
