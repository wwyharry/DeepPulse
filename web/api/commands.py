"""斜杠命令转发 —— /help /stats /memory /strategy /watchlist /portfolio /chart /fetch /stop"""

import asyncio
import base64
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from web.api.common import error_response

router = APIRouter()

# 命令帮助文本（与 TUI COMMAND_HELP 同源；tui 不可导入时用内置副本）
try:
    from deeppulse.agent.tui.app import COMMAND_HELP
except Exception:  # pragma: no cover - textual 缺失时降级
    COMMAND_HELP = """可用命令

  /help              显示此帮助
  /fetch [子命令]     数据采集 (stocks/kline/index/timeframe/all/update/status，无需预先下载数据)
  /chart <代码> [天数] 生成K线图（缺省代码取会话最近讨论的股票）
  /memory [子命令]    记忆管理 (list/search/predictions/profile)
  /strategy [关键词]  战法库搜索
  /watchlist          查看自选股
  /portfolio          查看持仓
  /stats              显示会话统计
  /stop               停止当前分析
  /clear              清屏
  reset               重置对话
"""

# /fetch 后台作业表（内存态）：job_id -> {"lines": [...], "done": bool}
_FETCH_JOBS: dict = {}
_FETCH_JOBS_LOCK = threading.Lock()


class CommandIn(BaseModel):
    arg: str | None = None
    session_id: str | None = None


@router.post("/api/commands/{name}")
async def run_command(request: Request, name: str, body: CommandIn | None = None):
    """前端斜杠命令转发。clear/reset 由前端本地处理（返回 404）。"""
    name = (name or "").lower().lstrip("/")
    arg = (body.arg if body else None) or ""
    session_id = (body.session_id if body else None) or ""

    if name in ("clear", "reset"):
        return error_response("该命令由前端本地处理", status_code=404)

    handler = {
        "help": _cmd_help,
        "stats": _cmd_stats,
        "memory": _cmd_memory,
        "strategy": _cmd_strategy,
        "watchlist": _cmd_watchlist,
        "portfolio": _cmd_portfolio,
        "chart": _cmd_chart,
        "fetch": _cmd_fetch,
        "stop": _cmd_stop,
    }.get(name)
    if handler is None:
        return error_response(f"未知命令: {name}", status_code=404)
    return await handler(request, arg, session_id)


async def _cmd_help(request: Request, arg: str, session_id: str) -> dict:
    return {"display": COMMAND_HELP}


async def _cmd_stats(request: Request, arg: str, session_id: str) -> dict:
    """会话统计：消息数/工具数/compaction/_session_stocks/topics"""
    app = request.app
    pool = app.state.agent_pool
    agent = pool.runtime(session_id).agent if session_id else None
    if agent is not None:
        messages = list(getattr(agent, "messages", []) or [])
        stocks = list(getattr(agent, "_session_stocks", []) or [])
        topics = list(getattr(agent, "_session_topics", []) or [])
        compaction = getattr(agent, "_compaction_stats", {}) or {}
        sid = getattr(agent, "session_id", session_id) or session_id
    else:
        stored = await asyncio.to_thread(app.state.session_store.load, session_id) if session_id else None
        if stored is None:
            return {"display": "⚠️ 会话不存在或尚未开始对话"}
        messages = list(stored.get("messages") or [])
        stocks = list(stored.get("session_stocks") or [])
        topics = list(stored.get("session_topics") or [])
        compaction = {}
        sid = session_id

    msg_count = len(messages)
    tool_count = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "tool")
    folded_msgs = compaction.get("folded_messages", 0)
    folded_calls = compaction.get("folded_tool_calls", 0)
    display = (
        f"会话统计\n\n"
        f"  会话ID: {str(sid)[:8]}\n"
        f"  消息总数: {msg_count}\n"
        f"  工具调用: {tool_count} 次\n"
        f"  历史折叠: {folded_msgs} 条（含 {folded_calls} 次工具调用）\n"
        f"  跟踪股票: {', '.join(stocks) or '无'}\n"
        f"  讨论主题: {', '.join(topics) or '无'}"
    )
    return {"display": display}


async def _cmd_memory(request: Request, arg: str, session_id: str) -> dict:
    """记忆管理：list|search X|predictions|profile（knowledge 未实现，不提供）"""
    import json as _json

    app = request.app
    pool = app.state.agent_pool
    agent = pool.runtime(session_id).agent if session_id else None
    memory = getattr(agent, "memory", None) or app.state.singletons.memory
    if memory is None:
        return {"display": "⚠️ 记忆系统不可用"}

    parts = arg.strip().split(maxsplit=1) if arg.strip() else [""]
    sub = parts[0]
    sub_arg = parts[1] if len(parts) > 1 else ""

    def _run():
        if sub in ("", "list"):
            data = _json.loads(memory.list_memories(limit=15))
            if not data.get("results"):
                return "暂无长期记忆"
            lines = [f"共 {data['total']} 条记忆:\n"]
            for m in data["results"]:
                lines.append(f"  [{m['memory_type']}] {m['content'][:80]}...")
            return "\n".join(lines)
        if sub == "search":
            if not sub_arg:
                return "用法: /memory search <关键词>"
            data = _json.loads(memory.search_memories(sub_arg))
            if not data.get("results"):
                return f"未找到与「{sub_arg}」相关的记忆"
            lines = [f"找到 {data['total']} 条相关记忆:\n"]
            for m in data["results"]:
                score = m.get("score", 0)
                lines.append(f"  [{m['memory_type']}] {m['content'][:60]}... (相关度: {score:.2f})")
            return "\n".join(lines)
        if sub == "predictions":
            data = _json.loads(memory.prediction_tracker.get_accuracy_stats())
            return (
                f"预测准确率:\n"
                f"  总验证: {data.get('total_verified', 0)} | "
                f"正确: {data.get('correct', 0)} | "
                f"准确率: {data.get('accuracy', 'N/A')}"
            )
        if sub == "profile":
            data = _json.loads(memory.user_profile.get_profile())
            profile = data.get("profile", {})
            if not profile:
                return "暂无用户画像数据"
            lines = ["用户交易画像:\n"]
            for key, info in profile.items():
                conf = info.get("confidence", 0)
                lines.append(f"  {key}: {info['value']} (置信度: {conf:.0%})")
            return "\n".join(lines)
        return "可用子命令: list, search, predictions, profile"

    try:
        display = await asyncio.to_thread(_run)
    except Exception as e:
        display = f"记忆命令执行失败: {e}"
    return {"display": display}


async def _cmd_strategy(request: Request, arg: str, session_id: str) -> dict:
    """战法库：空(前15)|search X|<名称>（前 500 字符）"""

    def _run():
        from deeppulse.agent.strategy_loader import get_strategy_loader

        loader = get_strategy_loader()
        if arg.startswith("search "):
            query = arg[7:]
            results = loader.search(query)
            if not results:
                return f"未找到匹配「{query}」的战法"
            lines = [f"找到 {len(results)} 个匹配战法:\n"]
            for r in results:
                lines.append(f"  [{r['score']:.1f}] {r['name']} ({r['file']})")
            return "\n".join(lines)
        if arg:
            content = loader.get_strategy_content(arg)
            if content:
                display = content[:500] + ("..." if len(content) > 500 else "")
                return f"{arg}:\n{display}"
            return f"未找到战法: {arg}"
        strategies = loader.list_strategies()
        if not strategies:
            return "暂无战法。请将 .md 文件放入 deeppulse/strategy/strategies/"
        lines = [f"共 {len(strategies)} 个战法:\n"]
        for s in strategies[:15]:
            tags = ", ".join(s["tags"][:2]) if s.get("tags") else ""
            lines.append(f"  {s['name']} [{tags}]")
        return "\n".join(lines)

    try:
        display = await asyncio.to_thread(_run)
    except Exception as e:
        display = f"战法命令执行失败: {e}"
    return {"display": display}


async def _cmd_watchlist(request: Request, arg: str, session_id: str) -> dict:
    """自选股：list|add X|remove X（写后返回最新 list 供刷新）"""
    s = request.app.state.singletons
    sub, _, rest = (arg or "").strip().partition(" ")
    sub = sub.lower()
    rest = rest.strip()

    def _list_text():
        rows = s.watchlist.list()
        if not rows:
            return "当前无自选股"
        lines = ["当前自选股:\n"]
        for r in rows:
            lines.append(f"  {r.get('name') or r.get('code')}({r.get('code')}) 分组:{r.get('group_name') or '默认'}")
        return "\n".join(lines)

    if sub in ("", "list"):
        display = await asyncio.to_thread(_list_text)
        return {"display": display, "refresh_watchlist": False}
    if sub == "add":
        code = rest.split()[0] if rest else ""
        if not code:
            return {"display": "用法: /watchlist add <代码>", "refresh_watchlist": False}
        result = await asyncio.to_thread(s.watchlist.add, code)
        if isinstance(result, dict) and result.get("status") == "ok":
            display = f"已添加 {result.get('name', code)}({result.get('code', code)})"
        else:
            display = f"添加失败: {(result or {}).get('message', result)}"
    elif sub == "remove":
        code = rest
        if not code:
            return {"display": "用法: /watchlist remove <代码>", "refresh_watchlist": False}
        result = await asyncio.to_thread(s.watchlist.remove, code)
        if isinstance(result, dict) and result.get("status") == "ok":
            display = f"已移除 {result.get('name', code)}({result.get('code', code)})"
        else:
            display = f"移除失败: {(result or {}).get('message', result)}"
    else:
        return {"display": "用法: /watchlist [list|add <代码>|remove <代码>]", "refresh_watchlist": False}

    listing = await asyncio.to_thread(_list_text)
    return {"display": f"{display}\n\n{listing}", "refresh_watchlist": True}


async def _cmd_portfolio(request: Request, arg: str, session_id: str) -> dict:
    """持仓：list|history|review"""
    s = request.app.state.singletons
    sub = (arg or "").strip().lower()

    def _run():
        from deeppulse.agent.journal import (
            format_portfolio_status,
            format_trade_history,
            generate_auto_review,
        )

        if sub in ("", "list"):
            return f"持仓:\n{format_portfolio_status(s.journal)}"
        if sub == "history":
            return f"交易记录:\n{format_trade_history(s.journal, days=14)}"
        if sub == "review":
            return f"周度复盘:\n{generate_auto_review(s.journal, 'week')}"
        return "用法: /portfolio [list|history|review]"

    try:
        display = await asyncio.to_thread(_run)
    except Exception as e:
        display = f"持仓命令执行失败: {e}"
    return {"display": display, "refresh_portfolio": True}


async def _cmd_chart(request: Request, arg: str, session_id: str) -> dict:
    """生成 K 线图：auto_open=False，返回图片字节 base64"""
    parts = (arg or "").split()
    code = parts[0] if parts else ""
    days = 120
    if not code:
        # 缺省代码取会话最近讨论的股票
        stocks = []
        if session_id:
            rt = request.app.state.agent_pool.runtime(session_id)
            if rt.agent is not None:
                stocks = list(getattr(rt.agent, "_session_stocks", []) or [])
            if not stocks:
                stored = await asyncio.to_thread(request.app.state.session_store.load, session_id)
                stocks = list((stored or {}).get("session_stocks") or [])
        for s_ in reversed(stocks):
            if str(s_).isdigit() and len(str(s_)) == 6:
                code = str(s_)
                break
    if not code:
        return error_response("用法: /chart <代码> [天数]，如 /chart 600519 120")
    if len(parts) > 1:
        try:
            days = max(1, int(parts[1]))
        except ValueError:
            return error_response("用法: /chart <代码> [天数]，天数须为正整数")

    def _gen():
        from deeppulse.agent.charts import generate_kline_chart

        return generate_kline_chart(code, days=days, auto_open=False)

    try:
        path = await asyncio.to_thread(_gen)
    except Exception as e:
        return error_response(f"K线图生成失败: {e}")
    path = str(path or "")
    if not path or path.startswith("错误"):
        return error_response(path or f"未找到 {code} 的K线数据")

    p = Path(path)
    if not p.exists():
        return error_response(f"K线图文件不存在: {path}")
    data = await asyncio.to_thread(p.read_bytes)
    mime = "image/png" if p.suffix.lower() == ".png" else "text/html"
    return {
        "image_base64": base64.b64encode(data).decode("ascii"),
        "mime": mime,
        "path": path,
    }


def _run_fetch_job(job_id: str, arg: str) -> None:
    """后台线程执行 /fetch，逐行日志进 job 表"""
    from deeppulse.agent.fetch_commands import run_fetch_command

    def log(line):
        with _FETCH_JOBS_LOCK:
            job = _FETCH_JOBS.get(job_id)
            if job is not None:
                job["lines"].append(str(line))

    try:
        result = run_fetch_command(arg, log=log)
        log(result)
    except Exception as e:
        log(f"fetch 执行失败: {e}")
    finally:
        with _FETCH_JOBS_LOCK:
            job = _FETCH_JOBS.get(job_id)
            if job is not None:
                job["done"] = True


async def _cmd_fetch(request: Request, arg: str, session_id: str) -> dict:
    """数据采集：后台 job + 日志轮询（help/status 也走 job，完成后即可见）"""
    job_id = uuid.uuid4().hex[:12]
    with _FETCH_JOBS_LOCK:
        _FETCH_JOBS[job_id] = {"lines": [], "done": False, "t0": time.time()}
        # 只保留最近 50 个 job，防内存泄漏
        if len(_FETCH_JOBS) > 50:
            for old in sorted(_FETCH_JOBS, key=lambda k: _FETCH_JOBS[k].get("t0", 0))[: len(_FETCH_JOBS) - 50]:
                _FETCH_JOBS.pop(old, None)
    threading.Thread(target=_run_fetch_job, args=(job_id, arg), daemon=True).start()
    return {"job_id": job_id}


@router.get("/api/commands/fetch/logs")
async def fetch_logs(job_id: str = Query(...)):
    """轮询 /fetch 作业日志"""
    with _FETCH_JOBS_LOCK:
        job = _FETCH_JOBS.get(job_id)
        if job is None:
            return error_response("作业不存在", status_code=404)
        return {"lines": list(job["lines"]), "done": bool(job["done"])}


async def _cmd_stop(request: Request, arg: str, session_id: str) -> dict:
    """等价 POST /api/chat/stop（幂等）"""
    if session_id:
        request.app.state.agent_pool.cancel_running(session_id)
    return {"status": "ok"}
