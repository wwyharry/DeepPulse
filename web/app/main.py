"""DeepPulse Web 后端 — FastAPI 入口"""

import sys
import traceback
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web.app.api import analysis, market, memory, portfolio, strategy, strategy_llm, system
from web.app.ws import chat_handler, realtime_handler, strategy_handler

# 静态文件目录
STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    print("🚀 DeepPulse Web 启动中...")
    yield
    print("👋 DeepPulse Web 关闭")


app = FastAPI(
    title="DeepPulse",
    description="A 股短线分析 AI Agent — Web API",
    version="0.2.2",
    lifespan=lifespan,
)


# 全局异常处理 - 确保所有错误都返回JSON格式
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器，确保返回JSON格式的错误信息"""
    print(f"[ERROR] {request.method} {request.url.path}: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "detail": "服务器内部错误，请检查后端日志",
        },
    )


# CORS — 本地开发允许前端 dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST API 路由
app.include_router(market.router, prefix="/api/market", tags=["行情"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["分析"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["组合"])
app.include_router(memory.router, prefix="/api/memory", tags=["记忆"])
app.include_router(system.router, prefix="/api/system", tags=["系统"])
app.include_router(strategy.router, prefix="/api/strategy", tags=["策略"])
app.include_router(strategy_llm.router, prefix="/api/strategy/llm", tags=["LLM策略"])

# 静态资源（CSS/JS 等共享设计系统文件）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# WebSocket 路由
app.include_router(chat_handler.router, prefix="/ws")
app.include_router(realtime_handler.router, prefix="/ws")
app.include_router(strategy_handler.router, prefix="/ws")


@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端页面"""
    html_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/strategy", response_class=HTMLResponse)
async def strategy_page():
    """返回策略平台页面"""
    html_file = STATIC_DIR / "strategy.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>策略平台页面不存在</h1>", status_code=404)


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}
