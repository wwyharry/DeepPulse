"""评判Agent - 全流程质量评测与纠错引导"""

import json

from agent.client import LLMClient

JUDGE_SYSTEM_PROMPT = """你是 DeepPulse 的评判Agent（Judge Agent），负责对主分析Agent的**完整输出流程**进行全面质量评测。

## 核心职责
你将收到主Agent的完整对话记录（包含每一轮的思考、工具调用、工具返回数据、最终结论），需要进行**全流程质量把关**：

### 1. 数据核实（最重要）
- 逐一核对主Agent**引用的数据**与**工具实际返回的数据**是否一致
- 检查是否编造了未通过工具获取的数据
- 检查数据时效性（是否用了过期数据做分析）

### 2. 推理链完整性
- 主Agent是否充分利用了获取到的所有数据？
- 是否存在"获取了数据但没分析"或"没获取数据就下结论"的情况？
- 多轮推理中观点是否自洽？有无前后矛盾？

### 3. 工具使用合理性
- 是否调用了足够覆盖问题所需的工具？
- 是否遗漏了关键工具（如分析股票却没查技术指标/成交量）？
- 工具参数是否合理？

### 4. 风险覆盖
- 是否提及了主要风险因素？
- 是否存在过度乐观/悲观的倾向？
- 结论是否有过度绝对化的表述（如"一定会涨"）？

### 5. 操作建议合理性
- 买卖建议是否有明确的数据支撑？
- 是否给出了仓位/止损等风控建议？
- 建议是否适合用户的分析场景？

## 输出格式（严格遵守）

📊 **评测报告**

━━━ 综合评分: X/10 ━━━

**✅ 做得好的：**
- [具体优点1]
- [具体优点2]

**⚠️ 需要注意的 N 个问题：**

1. ⚠️ **[问题类型]** [具体描述]
   💡 建议：[改进方向]

2. 📊 **[问题类型]** [具体描述]
   💡 建议：[改进方向]

...

**📊 数据核实：**
- 本轮共调用 N 个工具，获取了 M 条关键数据
- 主Agent引用了 K 条数据，其中：✅ X条准确 / ⚠️ Y条存疑 / ❌ Z条错误
- [如有错误] 具体错误：[列出]

**🎯 改进建议：**
- [最重要的改进方向]

## 问题类型标记
- ⚠️ 风险遗漏
- ❓ 逻辑矛盾
- 📊 数据错误
- 🔧 工具使用不足
- 💭 信息缺失
- 🎯 建议不合理

## 评判原则
- **实事求是**：对照工具返回的原始数据核实，不凭印象评判
- **保守原则**：宁可多提醒风险，不可漏掉问题
- **建设性**：指出问题的同时给出改进方向
- **简洁**：只指出关键问题，不重复主Agent已说的内容
- **尊重用户**：最终判断权在用户，不强加观点"""


class JudgeAgent:
    """评判Agent - 全流程质量评测"""

    def __init__(self, setting: dict = None):
        self.client = LLMClient(setting)
        self.setting = setting or self.client.setting

    async def judge_stream_async(self, messages: list):
        """流式评判主Agent的完整分析流程

        Args:
            messages: 主Agent的完整 messages 列表（包含 system/user/assistant/tool 消息）

        Yields:
            (event_type, content) 元组
            event_type: "content" | "issues_found" | "score" | "done"
        """
        context = self._build_judge_context(messages)

        judge_messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

        import re as _re

        content_buffer = ""
        issue_count = 0
        score_emitted = False

        async for chunk in self.client.chat_stream_async(judge_messages, tools=None):
            if chunk.type == "content":
                content_buffer += chunk.text
                yield ("content", chunk.text)

                # 实时检测问题数量
                stripped = chunk.text.strip()
                for i in range(1, 10):
                    if stripped.startswith(f"{i}. "):
                        issue_count += 1
                        yield (
                            "issues_found",
                            json.dumps({"count": issue_count, "partial": content_buffer}, ensure_ascii=False),
                        )
                        break

                # 检测评分（只触发一次）
                if not score_emitted and "综合评分" in content_buffer:
                    score_match = _re.search(r"综合评分[：:]\s*(\d+(?:\.\d+)?)\s*/\s*10", content_buffer)
                    if score_match:
                        score_emitted = True
                        yield ("score", score_match.group(1))

        yield ("done", "")

    def _build_judge_context(self, messages: list) -> str:
        """从完整 messages 重建评测上下文

        遍历所有消息，按轮次整理出：
        - 用户问题
        - 每一轮：assistant 的推理 + 工具调用 + 工具返回数据
        - 最终结论
        """
        user_query = ""
        rounds = []  # 每轮: {"thinking": str, "tool_calls": [...], "tool_results": [...], "content": str}
        current_round = None

        for msg in messages:
            role = msg.get("role", "")

            # 跳过 system 消息
            if role == "system":
                continue

            # 用户消息
            if role == "user":
                # 检查是否是 Anthropic 格式的 tool_result（不是真正的用户输入）
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Anthropic tool_result 格式，附加到当前轮
                    if current_round:
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "tool_result":
                                current_round["tool_results"].append(
                                    {"tool_use_id": item.get("tool_use_id", ""), "content": item.get("content", "")}
                                )
                    continue
                if not user_query:
                    user_query = content
                continue

            # 助手消息
            if role == "assistant":
                # 开始新一轮
                if current_round is None or current_round.get("content") or current_round.get("tool_calls"):
                    current_round = {"thinking": "", "tool_calls": [], "tool_results": [], "content": ""}
                    rounds.append(current_round)

                # 提取 reasoning_content（DeepSeek 思维链）
                reasoning = msg.get("reasoning_content", "")
                if reasoning:
                    current_round["thinking"] += reasoning

                # 提取 content
                content = msg.get("content", "")
                if content:
                    current_round["content"] = content

                # 提取 tool_calls
                for tc in msg.get("tool_calls", []):
                    if isinstance(tc, dict):
                        # OpenAI 格式
                        func = tc.get("function", tc)
                        current_round["tool_calls"].append(
                            {
                                "id": tc.get("id", ""),
                                "name": func.get("name", tc.get("name", "unknown")),
                                "arguments": func.get("arguments", tc.get("arguments", {})),
                            }
                        )
                continue

            # 工具结果消息（OpenAI 格式: role=tool）
            if role == "tool":
                if current_round:
                    current_round["tool_results"].append(
                        {"tool_call_id": msg.get("tool_call_id", ""), "content": msg.get("content", "")}
                    )
                continue

        # 如果没有识别到任何轮次，回退到简单提取
        if not rounds:
            last_content = ""
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    last_content = msg["content"]
                    break
            return self._build_fallback_context(user_query, last_content)

        # 构建结构化上下文
        return self._build_structured_context(user_query, rounds)

    def _build_structured_context(self, user_query: str, rounds: list) -> str:
        """构建结构化评测上下文"""
        parts = []
        parts.append(f"## 用户问题\n{user_query}\n")

        # 统计信息
        total_tool_calls = sum(len(r["tool_calls"]) for r in rounds)
        total_tool_results = sum(len(r["tool_results"]) for r in rounds)
        tools_used = set()
        for r in rounds:
            for tc in r["tool_calls"]:
                tools_used.add(tc["name"])

        parts.append("## 分析概况")
        parts.append(f"- 推理轮次: {len(rounds)} 轮")
        parts.append(f"- 工具调用: {total_tool_calls} 次")
        parts.append(f"- 工具返回: {total_tool_results} 条")
        parts.append(f"- 使用工具: {', '.join(sorted(tools_used)) if tools_used else '无'}\n")

        # 每轮详情
        for i, r in enumerate(rounds):
            parts.append(f"### 第 {i + 1} 轮")

            # 思考过程（截取关键部分，避免过长）
            if r["thinking"]:
                thinking = r["thinking"]
                if len(thinking) > 1500:
                    thinking = thinking[:800] + "\n...(中间省略)...\n" + thinking[-500:]
                parts.append(f"**🧠 思考过程:**\n```\n{thinking}\n```")

            # 工具调用 + 结果
            if r["tool_calls"]:
                parts.append(f"**🔧 工具调用 ({len(r['tool_calls'])} 个):**")
                for j, tc in enumerate(r["tool_calls"]):
                    args_str = self._format_args(tc["arguments"])
                    parts.append(f"  {j + 1}. `{tc['name']}({args_str})`")

                    # 查找对应的工具结果
                    result = self._find_tool_result(tc, r["tool_results"], rounds, i, j)
                    if result:
                        # 截断过长的结果
                        display_result = result
                        if len(display_result) > 600:
                            display_result = display_result[:400] + f"\n...[截断，原始 {len(result)} 字符]"
                        parts.append(f"     ↳ 返回:\n```\n{display_result}\n```")
                    else:
                        parts.append("     ↳ 返回: [未找到结果]")

            # 助手结论
            if r["content"]:
                content = r["content"]
                # 最后一轮显示完整内容，前面的轮次截取
                if i < len(rounds) - 1 and len(content) > 500:
                    content = content[:300] + f"\n...[截断，原始 {len(r['content'])} 字符]"
                parts.append(f"**📝 分析输出:**\n{content}")

            parts.append("")

        # 最终结论单独高亮
        final_content = ""
        for r in reversed(rounds):
            if r["content"]:
                final_content = r["content"]
                break
        if final_content:
            parts.append(f"## 最终结论（完整）\n{final_content}\n")

        parts.append("---\n请按照你的职责对以上**完整流程**进行评测，输出结构化评测报告。")
        return "\n".join(parts)

    def _format_args(self, arguments) -> str:
        """格式化工具参数"""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                return arguments[:100] if len(arguments) > 100 else arguments
        if isinstance(arguments, dict):
            # 只显示关键参数，避免过长
            short = {}
            for k, v in arguments.items():
                sv = str(v)
                short[k] = sv[:50] + "..." if len(sv) > 50 else v
            return json.dumps(short, ensure_ascii=False)
        return str(arguments)[:100]

    def _find_tool_result(
        self, tool_call: dict, current_results: list, rounds: list, round_idx: int, call_idx: int
    ) -> str:
        """查找工具调用对应的结果

        优先在当前轮的 tool_results 中按 tool_call_id 匹配，
        找不到则按顺序匹配。
        """
        tc_id = tool_call.get("id", "")

        # 按 ID 精确匹配（OpenAI 格式）
        if tc_id:
            for tr in current_results:
                if tr.get("tool_call_id") == tc_id or tr.get("tool_use_id") == tc_id:
                    content = tr.get("content", "")
                    if isinstance(content, list):
                        # Anthropic 格式
                        text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
                        return "\n".join(text_parts)
                    return str(content)

        # 按顺序模糊匹配（兜底）
        if call_idx < len(current_results):
            content = current_results[call_idx].get("content", "")
            if isinstance(content, list):
                text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
                return "\n".join(text_parts)
            return str(content)

        return ""

    def _build_fallback_context(self, user_query: str, last_content: str) -> str:
        """兜底：简单上下文（当无法解析完整流程时）"""
        return f"""请评判以下分析：

**用户问题**：{user_query or "（未识别到）"}

**主Agent的最终分析**：
{last_content or "（无内容）"}

---

⚠️ 注意：由于消息格式解析限制，未能提取完整的工具调用和返回数据。
请基于可见内容进行评判，并提示用户可能需要补充数据核实。

请按照你的职责进行评判，输出结构化的评测报告。
"""
