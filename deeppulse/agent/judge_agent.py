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
- 简单查询（如"贵州茅台多少钱"）→ 重点看回答准确性，不需要深度分析
- 技术分析（如"分析贵州茅台走势"）→ 重点看工具使用、数据覆盖、逻辑推理
- 策略回测（如"回测MA金叉策略"）→ 重点看回测参数、结果解读
- 综合分析（如"贵州茅台值得买吗"）→ 重点看多维度分析、风险提示

## 评分方式

根据问题类型，给出 1-10 分评价：
- 9-10：优秀，完全满足需求
- 7-8：良好，基本满足需求，有小改进空间
- 5-6：一般，有明显不足
- 3-4：较差，遗漏重要内容
- 1-2：很差，分析有严重问题

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
- 根据问题复杂度调整期望
- 不要对简单问题要求深度分析
- 不要对复杂问题只看表面
- 给出具体可操作的建议"""


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
                    score_match = re.search(r"评分[：:]\s*(\d+(?:\.\d+)?)\s*/\s*10", content_buffer)
                    if score_match:
                        score_emitted = True
                        yield ("score", score_match.group(1))

        # 生成摘要
        summary = self._generate_summary(content_buffer)
        yield ("summary", json.dumps(summary, ensure_ascii=False))
        yield ("done", "")

    def _build_judge_context(self, messages: list) -> str:
        """从 messages 构建评测上下文"""
        user_query = ""
        rounds = []
        current_round = None

        for msg in messages:
            role = msg.get("role", "")

            if role == "system":
                continue

            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    if current_round:
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "tool_result":
                                current_round["tool_results"].append({
                                    "content": item.get("content", "")
                                })
                    continue
                if not user_query:
                    user_query = content
                continue

            if role == "assistant":
                if current_round is None or current_round.get("content") or current_round.get("tool_calls"):
                    current_round = {"tool_calls": [], "tool_results": [], "content": ""}
                    rounds.append(current_round)

                content = msg.get("content", "")
                if content:
                    current_round["content"] = content

                for tc in msg.get("tool_calls", []):
                    if isinstance(tc, dict):
                        func = tc.get("function", tc)
                        current_round["tool_calls"].append({
                            "name": func.get("name", tc.get("name", "unknown")),
                        })
                continue

            if role == "tool":
                if current_round:
                    content = msg.get("content", "")
                    current_round["tool_results"].append({
                        "content": content[:300] + "..." if len(content) > 300 else content
                    })
                continue

        if not rounds:
            last_content = ""
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    last_content = msg["content"]
                    break
            return f"## 用户问题\n{user_query}\n\n## 分析结论\n{last_content}\n\n请评估以上分析。"

        return self._build_context(user_query, rounds)

    def _build_context(self, user_query: str, rounds: list) -> str:
        """构建精简上下文"""
        parts = []
        parts.append(f"## 用户问题\n{user_query}\n")

        # 统计
        total_tools = sum(len(r["tool_calls"]) for r in rounds)
        tools_used = set()
        for r in rounds:
            for tc in r["tool_calls"]:
                tools_used.add(tc["name"])

        parts.append(f"## 分析概况")
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
        score_match = re.search(r"评分[：:]\s*(\d+(?:\.\d+)?)", content)
        score = float(score_match.group(1)) if score_match else 0

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

        scores = [h.get("summary", {}).get("score", 0) for h in history]
        types = {}
        for h in history:
            t = h.get("summary", {}).get("type", "未知")
            types[t] = types.get(t, 0) + 1

        return {
            "total": len(history),
            "avg_score": sum(scores) / len(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "min_score": min(scores) if scores else 0,
            "type_distribution": types,
        }
