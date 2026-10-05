"""评判Agent - 自适应评测分析质量"""

import json
import re
import time
from datetime import datetime

from deeppulse.agent.client import LLMClient

# ═══════════════════════════════════════════════════════════
# 评测系统 Prompt（自适应版）
# ═══════════════════════════════════════════════════════════

JUDGE_SYSTEM_PROMPT = """你是 DeepPulse 的评测Agent，根据用户问题类型自适应评估分析质量。

## 核心原则

**不同问题，不同标准**：
- 简单查询（如"贵州茅台多少钱"）→ 重点看回答准确性、响应是否切题，不要求深度分析
- 技术分析（如"分析贵州茅台走势"）→ 重点看数据覆盖、工具使用、逻辑推理、关键价位与风险提示
- 策略回测（如"回测MA金叉策略"）→ 重点看回测参数是否合理、指标解读是否准确、结论是否被数据支撑
- 综合分析（如"贵州茅台值得买吗"）→ 重点看多维度分析（技术/资金/消息/情绪）、风险提示、建议可操作性

**一票否决项（任一触发，总分不超过 4 分）**：
- 编造数据：出现工具未返回的数字、价格、日期却当作事实陈述
- 结论与数据矛盾：给出的建议与自己引用的指标方向相反且无解释
- 回避问题：答非所问或遗漏用户明确问到的核心诉求

## 评分方式

根据问题类型，给出 1-10 分评价：
- 9-10：优秀。数据准确完整，推理严密，结论有数据支撑，风险提示到位，完全满足需求
- 7-8：良好。主体正确，有小的遗漏或表述瑕疵，基本满足需求
- 5-6：一般。有明显不足（如漏掉资金面/消息面、关键价位含糊、建议笼统）
- 3-4：较差。遗漏重要内容，或存在数据误读、逻辑跳跃
- 1-2：很差。分析有严重问题（编造数据、结论错误、严重跑题）

评分必须与问题复杂度匹配：对简单查询不要因缺少深度分析而扣分；对复杂分析不要因"看起来完整"而给高分——逐条核对数据与推理。

## 输出格式

📊 **评测结果**

**评分：X/10**

**问题类型：**[简单查询/技术分析/策略回测/综合分析/...]

**✅ 做得好：**
- [具体优点]

**⚠️ 可改进：**
- [具体建议]

**📝 总评：**
[一段话总结]

## 要求
- 评分行必须严格写成「**评分：X/10**」（X 为 1-10 的数字），供系统解析
- 逐条核对回复中引用的数据与工具返回是否一致，发现编造立即指出并触发一票否决
- 根据问题复杂度调整期望，不要对简单问题要求深度分析
- 不要对复杂问题只看表面，逐维度核对
- 做得好/可改进各给 2-4 条，必须具体（引用回复中的原句或数据），不写空话
- 给出的改进建议必须可操作（如"补上止损位"而非"注意风险"）"""


# ═══════════════════════════════════════════════════════════
# 评分解析（实时流式 / 终态全文共用）
# ═══════════════════════════════════════════════════════════

# 流式：必须等到 "/10" 或 "分" 出现数字才算完整，避免 "8.5" 流到一半在 "8" 处提前锁存
_SCORE_RE_STREAM = re.compile(r"评分[：:]\s*(\d+(?:\.\d+)?)(?:\s*/\s*10|\s*分)")
# 终态全文：内容已完整，宽松即可（兼容 "评分：8分"、截断的 "评分：8"）
_SCORE_RE_FINAL = re.compile(r"评分[：:]\s*(\d+(?:\.\d+)?)")


def _parse_score(content: str, streaming: bool = False):
    """解析评分；解析失败或越界返回 None，绝不伪造 0 分。"""
    m = (_SCORE_RE_STREAM if streaming else _SCORE_RE_FINAL).search(content)
    if not m:
        return None
    v = float(m.group(1))
    return v if 0.0 <= v <= 10.0 else None


def _text_of(content):
    """归一化消息 content 为 (纯文本, 工具名列表)。兼容 OpenAI str 与 Anthropic block list。"""
    tool_names = []
    if isinstance(content, str):
        return content, tool_names
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                parts.append(b.get("text", ""))
            elif t == "tool_use":
                name = b.get("name")
                if name:
                    tool_names.append(name)
        return "\n".join(parts), tool_names
    return str(content), tool_names


# ═══════════════════════════════════════════════════════════
# 评测 Agent 类
# ═══════════════════════════════════════════════════════════


class JudgeAgent:
    """评判Agent - 自适应评测"""

    def __init__(self, setting: dict = None):
        self.client = LLMClient(setting)
        self.setting = setting or self.client.setting

    async def judge_stream_async(self, messages: list):
        """自适应流式评测

        Args:
            messages: 主Agent的完整 messages 列表

        Yields:
            (event_type, content) 元组
        """
        context = self._build_judge_context(messages)

        judge_messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

        content_buffer = ""
        score_emitted = False

        async for chunk in self.client.chat_stream_async(judge_messages, tools=None):
            if chunk.type == "content":
                content_buffer += chunk.text
                yield ("content", chunk.text)

                # 实时检测评分
                if not score_emitted:
                    v = _parse_score(content_buffer, streaming=True)
                    if v is not None:
                        score_emitted = True
                        yield ("score", f"{v:g}")

        # 生成摘要
        summary = self._generate_summary(content_buffer)
        yield ("summary", json.dumps(summary, ensure_ascii=False))
        yield ("done", "")

    def _build_judge_context(self, messages: list) -> str:
        """从 messages 构建评测上下文"""
        from deeppulse.agent.history import FOLD_MARKER

        user_query = ""
        folded_summary = ""
        rounds = []
        current_round = None

        for msg in messages:
            role = msg.get("role", "")

            if role == "system":
                content = str(msg.get("content", "") or "")
                # B7 折叠占位符：早前上下文摘要，注入评测上下文而非丢弃
                if content.startswith(FOLD_MARKER):
                    folded_summary = content
                continue

            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    if current_round:
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "tool_result":
                                current_round["tool_results"].append({"content": item.get("content", "")})
                    continue
                if not user_query:
                    user_query = content
                continue

            if role == "assistant":
                if current_round is None or current_round.get("content") or current_round.get("tool_calls"):
                    current_round = {"tool_calls": [], "tool_results": [], "content": ""}
                    rounds.append(current_round)

                content = msg.get("content", "")
                text, tool_names = _text_of(content)
                if text:
                    current_round["content"] = text  # 保证是 str，避免 list repr 灌入上下文
                for name in tool_names:  # anthropic tool_use 块提取工具名
                    current_round["tool_calls"].append({"name": name})

                for tc in msg.get("tool_calls", []):  # openai 路径保持不变
                    if isinstance(tc, dict):
                        func = tc.get("function", tc)
                        current_round["tool_calls"].append(
                            {
                                "name": func.get("name", tc.get("name", "unknown")),
                            }
                        )
                continue

            if role == "tool":
                if current_round:
                    content = msg.get("content", "")
                    current_round["tool_results"].append(
                        {"content": content[:300] + "..." if len(content) > 300 else content}
                    )
                continue

        if not rounds:
            last_content = ""
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    last_content, _ = _text_of(msg["content"])
                    break
            folded_part = f"\n## 早前上下文（已折叠）\n{folded_summary}\n" if folded_summary else ""
            return f"## 用户问题\n{user_query}\n{folded_part}\n## 分析结论\n{last_content}\n\n请评估以上分析。"

        return self._build_context(user_query, rounds, folded_summary)

    def _build_context(self, user_query: str, rounds: list, folded_summary: str = "") -> str:
        """构建精简上下文"""
        parts = []
        parts.append(f"## 用户问题\n{user_query}\n")
        if folded_summary:
            parts.append(f"## 早前上下文（已折叠）\n{folded_summary}\n")

        # 统计
        total_tools = sum(len(r["tool_calls"]) for r in rounds)
        tools_used = set()
        for r in rounds:
            for tc in r["tool_calls"]:
                tools_used.add(tc["name"])

        parts.append("## 分析概况")
        parts.append(f"- 推理轮次: {len(rounds)} 轮")
        parts.append(f"- 工具调用: {total_tools} 次")
        if tools_used:
            parts.append(f"- 使用工具: {', '.join(sorted(tools_used))}")
        parts.append("")

        # 每轮摘要
        for i, r in enumerate(rounds):
            parts.append(f"### 第 {i + 1} 轮")

            if r["tool_calls"]:
                tool_names = [tc["name"] for tc in r["tool_calls"]]
                parts.append(f"工具: {', '.join(tool_names)}")

            if r["content"]:
                content = r["content"]
                if len(content) > 1000:
                    content = content[:800] + "\n...[截断]"
                parts.append(f"输出:\n{content}")
            parts.append("")

        parts.append("---\n请根据问题类型自适应评估分析质量。")
        return "\n".join(parts)

    def _generate_summary(self, content: str) -> dict:
        """生成评测摘要"""
        # 解析失败/越界为 None（而非 0），避免把“解析失败”伪装成 0 分差评
        score = _parse_score(content)

        # 检测问题类型
        qtype = "未知"
        for t in ["简单查询", "技术分析", "策略回测", "综合分析"]:
            if t in content:
                qtype = t
                break

        return {
            "score": score,
            "type": qtype,
            "timestamp": time.time(),
        }


# ═══════════════════════════════════════════════════════════
# 评测历史管理
# ═══════════════════════════════════════════════════════════


class JudgeHistory:
    """评测历史管理"""

    def __init__(self, store_dir=None):
        from pathlib import Path

        if store_dir is None:
            store_dir = Path(__file__).parent.parent / "data" / "judge_history"
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def save_evaluation(self, session_id: str, summary: dict, report: str):
        """保存评测结果"""
        record = {
            "session_id": session_id,
            "timestamp": time.time(),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary,
            "report": report[:1500],
        }

        filename = f"{session_id}_{int(time.time())}.json"
        filepath = self.store_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        return str(filepath)

    def get_history(self, limit: int = 20) -> list:
        """获取评测历史"""
        files = sorted(self.store_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        history = []
        for f in files[:limit]:
            try:
                with open(f, encoding="utf-8") as fp:
                    history.append(json.load(fp))
            except Exception:
                continue
        return history

    def get_statistics(self) -> dict:
        """获取评测统计"""
        history = self.get_history(limit=100)
        if not history:
            return {"total": 0}

        # 过滤无效评分（None/非数值），区分总数与有效评分数
        scores = [s for h in history if isinstance(s := h.get("summary", {}).get("score"), (int, float))]
        types = {}
        for h in history:
            t = h.get("summary", {}).get("type", "未知")
            types[t] = types.get(t, 0) + 1

        return {
            "total": len(history),
            "scored_count": len(scores),
            "avg_score": sum(scores) / len(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "min_score": min(scores) if scores else 0,
            "type_distribution": types,
        }
