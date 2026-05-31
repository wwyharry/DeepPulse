"""CLI 入口 - 命令行对话界面，支持流式输出、thinking 展示、记忆系统"""
import sys
import io
import atexit
from pathlib import Path

# Windows 终端 UTF-8 支持
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

# ANSI 颜色
THINKING_COLOR = "\033[90m"  # 灰色
CONTENT_COLOR = "\033[0m"    # 默认
TOOL_COLOR = "\033[36m"      # 青色
ROUND_COLOR = "\033[33m"     # 黄色
RESET = "\033[0m"
BOLD = "\033[1m"


def stream_chat(agent, user_input: str, show_thinking: bool = True,
                show_tools: bool = True):
    """流式输出 Agent 对话"""
    in_thinking = False

    for chunk_type, text in agent.chat_stream(user_input):
        if chunk_type == "round":
            if show_tools:
                print(f"\n{ROUND_COLOR}[Round {text}]{RESET}", flush=True)

        elif chunk_type == "thinking":
            if show_thinking:
                if not in_thinking:
                    print(f"\n{THINKING_COLOR}💭 思考: ", end="", flush=True)
                    in_thinking = True
                print(f"{THINKING_COLOR}{text}{RESET}", end="", flush=True)

        elif chunk_type == "content":
            if in_thinking:
                print(f"{RESET}", flush=True)
                in_thinking = False
            print(f"{CONTENT_COLOR}{text}{RESET}", end="", flush=True)

        elif chunk_type == "tool_start":
            if in_thinking:
                print(f"{RESET}", flush=True)
                in_thinking = False
            if show_tools:
                print(f"\n{TOOL_COLOR}  🔧 {text}{RESET}", flush=True)

        elif chunk_type == "tool_result":
            if show_tools:
                display = text[:150] + "..." if len(text) > 150 else text
                print(f"{TOOL_COLOR}  📋 {display}{RESET}", flush=True)

        elif chunk_type == "done":
            if in_thinking:
                print(f"{RESET}", flush=True)
                in_thinking = False
            print(flush=True)


def _handle_memory_command(agent, cmd: str):
    """处理 /memory 系列命令"""
    import json

    parts = cmd.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if sub == "" or sub == "list":
        result = agent.memory.list_memories(limit=20)
        data = json.loads(result)
        if not data.get("results"):
            print(f"{TOOL_COLOR}  暂无长期记忆{RESET}")
        else:
            print(f"{TOOL_COLOR}  共 {data['total']} 条记忆:{RESET}")
            type_labels = {"preference": "偏好", "insight": "结论", "fact": "事实", "context": "上下文", "summary": "摘要"}
            for m in data["results"]:
                label = type_labels.get(m["memory_type"], m["memory_type"])
                print(f"  {TOOL_COLOR}[{label}] {m['content']} (ID: {m['id'][:8]}...){RESET}")

    elif sub == "search":
        if not arg:
            print(f"{TOOL_COLOR}  用法: /memory search <关键词>{RESET}")
            return
        result = agent.memory.search_memories(arg)
        data = json.loads(result)
        if not data.get("results"):
            print(f"{TOOL_COLOR}  未找到相关记忆{RESET}")
        else:
            print(f"{TOOL_COLOR}  找到 {data['total']} 条相关记忆:{RESET}")
            type_labels = {"preference": "偏好", "insight": "结论", "fact": "事实", "context": "上下文", "summary": "摘要"}
            for m in data["results"]:
                label = type_labels.get(m["memory_type"], m["memory_type"])
                score = m.get("score", 0)
                print(f"  {TOOL_COLOR}[{label}] {m['content'][:80]}... (相关度: {score:.2f}){RESET}")

    elif sub == "save":
        if not arg:
            print(f"{TOOL_COLOR}  用法: /memory save <内容>{RESET}")
            return
        result = agent.memory.save_memory(arg, memory_type="fact")
        data = json.loads(result)
        print(f"{TOOL_COLOR}  已保存记忆 ID: {data.get('memory_id', 'unknown')[:8]}...{RESET}")

    elif sub == "clear":
        confirm = input(f"{ROUND_COLOR}确认清除所有记忆？(yes/no): {RESET}")
        if confirm.lower() == "yes":
            import duckdb
            conn = duckdb.connect(str(agent.memory.db_path))
            conn.execute("DELETE FROM long_term_memories")
            conn.close()
            print(f"{TOOL_COLOR}  所有记忆已清除{RESET}")
        else:
            print(f"{TOOL_COLOR}  已取消{RESET}")

    elif sub == "predictions":
        result = agent.memory.prediction_tracker.get_accuracy_stats()
        data = json.loads(result)
        print(f"{TOOL_COLOR}  预测准确率统计:{RESET}")
        print(f"  {TOOL_COLOR}总验证: {data.get('total_verified', 0)} | "
              f"正确: {data.get('correct', 0)} | "
              f"错误: {data.get('wrong', 0)} | "
              f"部分: {data.get('partial', 0)} | "
              f"准确率: {data.get('accuracy', 'N/A')}{RESET}")
        for d, stats in data.get("by_direction", {}).items():
            print(f"  {TOOL_COLOR}  {d}: {stats}{RESET}")

    elif sub == "profile":
        result = agent.memory.user_profile.get_profile()
        data = json.loads(result)
        profile = data.get("profile", {})
        if not profile:
            print(f"{TOOL_COLOR}  暂无用户画像数据{RESET}")
        else:
            print(f"{TOOL_COLOR}  用户交易画像:{RESET}")
            for key, info in profile.items():
                conf = info.get("confidence", 0)
                print(f"  {TOOL_COLOR}{key}: {info['value']} (置信度: {conf:.0%}){RESET}")

    elif sub == "knowledge":
        result = agent.memory.knowledge_graph.query_related()
        data = json.loads(result)
        results = data.get("results", [])
        if not results:
            print(f"{TOOL_COLOR}  知识图谱暂无数据{RESET}")
        else:
            print(f"{TOOL_COLOR}  知识图谱 ({data.get('total', 0)} 条关系):{RESET}")
            for r in results[:10]:
                src = r["source"]["name"]
                tgt = r["target"]["name"]
                rel = r["relation"]
                w = r["weight"]
                print(f"  {TOOL_COLOR}{src} --[{rel}]--> {tgt} (权重: {w:.1f}){RESET}")

    else:
        print(f"{TOOL_COLOR}  可用子命令: list, search, save, clear, predictions, profile, knowledge{RESET}")


def _handle_strategy_command(cmd: str):
    """处理 /strategy 命令"""
    from agent.strategy_loader import get_strategy_loader, reload_strategies
    import json

    parts = cmd.strip().split(maxsplit=1)
    arg = parts[1] if len(parts) > 1 else ""

    loader = get_strategy_loader()

    if arg == "reload":
        loader = reload_strategies()
        print(f"{TOOL_COLOR}  已重新加载，共 {len(loader.strategies)} 个战法{RESET}")
        return

    if arg.startswith("search "):
        query = arg[7:]
        results = loader.search(query)
        if not results:
            print(f"{TOOL_COLOR}  未找到匹配的战法{RESET}")
        else:
            print(f"{TOOL_COLOR}  找到 {len(results)} 个匹配战法:{RESET}")
            for r in results:
                print(f"  {TOOL_COLOR}[{r['score']:.1f}] {r['name']} ({r['file']}){RESET}")
                print(f"       {r['match_reason']}{RESET}")
        return

    if arg:
        # 查看指定战法
        content = loader.get_strategy_content(arg)
        if content:
            print(f"{TOOL_COLOR}{content}{RESET}")
        else:
            print(f"{TOOL_COLOR}  未找到战法: {arg}{RESET}")
        return

    # 列出所有战法
    strategies = loader.list_strategies()
    if not strategies:
        print(f"{TOOL_COLOR}  暂无战法。请将 .md 文件放入 agent/strategies/ 目录{RESET}")
        print(f"{TOOL_COLOR}  参考 agent/strategies/README.md 了解格式{RESET}")
    else:
        print(f"{TOOL_COLOR}  共 {len(strategies)} 个战法:{RESET}")
        for s in strategies:
            tags = ", ".join(s["tags"][:3]) if s["tags"] else ""
            print(f"  {TOOL_COLOR}{s['name']} ({s['file']}) [{tags}]{RESET}")


def _handle_watchlist_command(cmd: str):
    """处理 /watchlist 命令"""
    from agent.watchlist import WatchlistManager, format_watchlist_status
    wl = WatchlistManager()

    parts = cmd.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "" or sub == "list":
        result = format_watchlist_status(wl)
        print(f"{TOOL_COLOR}{result}{RESET}")

    elif sub == "add" and len(parts) > 2:
        code = parts[2].strip().split()[0]
        result = wl.add(code)
        print(f"{TOOL_COLOR}  已添加 {result.get('name', code)}({code}){RESET}")

    elif sub == "remove" and len(parts) > 2:
        code = parts[2].strip()
        wl.remove(code)
        print(f"{TOOL_COLOR}  已移除 {code}{RESET}")

    elif sub == "groups":
        groups = wl.list_groups()
        if not groups:
            print(f"{TOOL_COLOR}  暂无自选股分组{RESET}")
        else:
            for g in groups:
                print(f"{TOOL_COLOR}  {g['group_name']}: {g['count']}只{RESET}")

    else:
        print(f"{TOOL_COLOR}  可用子命令: list, add <代码>, remove <代码>, groups{RESET}")


def _handle_portfolio_command(cmd: str):
    """处理 /portfolio 命令"""
    from agent.journal import TradeJournal, format_portfolio_status, format_trade_history
    j = TradeJournal()

    parts = cmd.strip().split(maxsplit=1)
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "" or sub == "list":
        result = format_portfolio_status(j)
        print(f"{TOOL_COLOR}{result}{RESET}")

    elif sub == "history":
        result = format_trade_history(j, days=14)
        print(f"{TOOL_COLOR}{result}{RESET}")

    elif sub == "review":
        from agent.journal import generate_auto_review
        result = generate_auto_review(j, "week")
        print(f"{TOOL_COLOR}{result}{RESET}")

    else:
        print(f"{TOOL_COLOR}  可用子命令: list, history, review{RESET}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="A股短线分析 AI Agent")
    parser.add_argument("question", nargs="?", help="直接提问（不进入交互模式）")
    parser.add_argument("--no-verbose", action="store_true", help="隐藏工具调用过程")
    parser.add_argument("--no-thinking", action="store_true", help="隐藏思考过程")
    parser.add_argument("--model", type=str, help="覆盖模型名称")
    parser.add_argument("--no-stream", action="store_true", help="禁用流式输出")
    args = parser.parse_args()

    from agent.agent import StockAgent
    from agent.client import load_setting

    setting_overrides = load_setting()
    if args.model:
        setting_overrides["llm"]["model"] = args.model
    if args.no_verbose:
        setting_overrides["agent"]["verbose"] = False

    agent = StockAgent(setting=setting_overrides,
                       verbose=not args.no_verbose if args.no_verbose else None)

    # 注册退出处理，确保会话摘要被保存
    atexit.register(agent.on_session_end)

    show_thinking = not args.no_thinking
    show_tools = not args.no_verbose

    # 单次提问模式
    if args.question:
        if args.no_stream:
            print(agent.chat(args.question))
        else:
            stream_chat(agent, args.question, show_thinking, show_tools)
        return

    # 交互模式
    print("=" * 50)
    print(f"{BOLD}A股短线分析 AI Agent{RESET}")
    print(f"模型: {setting_overrides['llm']['model']}")
    print(f"会话ID: {agent.session_id[:8]}...")
    print("输入股票名称或代码开始分析")
    print("命令: quit=退出 | reset=重置 | /thinking=切换思考显示")
    print("      /memory=查看记忆 | /memory search <关键词>=搜索记忆")
    print("      /memory predictions=预测准确率 | /memory profile=用户画像")
    print("      /memory knowledge=知识图谱 | /strategy=查看战法")
    print("      /watchlist=自选股 | /portfolio=持仓与交易")
    print("=" * 50)

    while True:
        try:
            user_input = input(f"\n{BOLD}你:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("再见！")
            break
        if user_input.lower() == "reset":
            agent.reset()
            print("对话已重置。")
            continue
        if user_input.lower() == "/thinking":
            show_thinking = not show_thinking
            print(f"思考过程显示: {'开' if show_thinking else '关'}")
            continue
        if user_input.lower().startswith("/memory") or user_input.lower().startswith("/mem"):
            _handle_memory_command(agent, user_input)
            continue
        if user_input.lower().startswith("/strategy") or user_input.lower().startswith("/战法"):
            _handle_strategy_command(user_input)
            continue
        if user_input.lower().startswith("/watchlist") or user_input.lower().startswith("/自选"):
            _handle_watchlist_command(user_input)
            continue
        if user_input.lower().startswith("/portfolio") or user_input.lower().startswith("/持仓"):
            _handle_portfolio_command(user_input)
            continue

        print(f"\n{BOLD}分析师:{RESET}", end="")
        stream_chat(agent, user_input, show_thinking, show_tools)


if __name__ == "__main__":
    main()
