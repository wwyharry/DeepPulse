"""DeepPulse Web 后端 — FastAPI 入口"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from web.app.api import analysis, market, memory, portfolio, system
from web.app.ws import chat_handler, realtime_handler

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

# WebSocket 路由
app.include_router(chat_handler.router, prefix="/ws")
app.include_router(realtime_handler.router, prefix="/ws")


@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端页面"""
    html_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}
