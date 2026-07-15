"""Agent 核心 - ReAct 循环、工具调度、流式输出、记忆系统、战法库"""

import asyncio
import json
import re
import uuid

from deeppulse import config as _config
from deeppulse.agent.client import LLMClient
from deeppulse.agent.memory import MemoryManager
from deeppulse.agent.prompts import SYSTEM_PROMPT
from deeppulse.agent.strategy_loader import get_strategy_loader
from deeppulse.agent.tools import TOOL_DEFINITIONS, TOOL_DISPATCH


class StockAgent:
    """股票短线分析 Agent"""

    def __init__(self, setting: dict = None, verbose: bool = None, session_id: str = None):
        self.client = LLMClient(setting)
        self.setting = setting or self.client.setting
        self.verbose = verbose if verbose is not None else self.setting["agent"]["verbose"]
        self.max_rounds = self.setting["agent"]["max_rounds"]

        # 记忆系统
        self.session_id = session_id or str(uuid.uuid4())
        self.memory = MemoryManager(setting=self.setting)
        self.memory.init_tables()
        self.memory.start_session(self.session_id)

        # 用户消息历史（用于画像提取）
        self._user_messages = []

        # 工具调用追踪（防重复、防编造）
        self._tool_call_history: list[tuple[str, str]] = []  # (tool_name, args_signature)
        self._consecutive_tool_errors: int = 0  # 连续工具异常计数
        self._truncated_tools: set[str] = set()  # 本轮被截断过的工具名

        # 跟踪会话中的股票和主题
        self._session_stocks = set()
        self._session_topics = set()
        self._pending_system_notes = []

        # 构建动态 system prompt（静态 + 记忆上下文 + 战法列表）
        memory_context = self.memory.get_context_block("", max_chars=2000)
        strategy_summary = get_strategy_loader().get_all_content_summary(max_chars=2000)
        system_content = SYSTEM_PROMPT
        if memory_context:
            system_content += "\n\n## 历史记忆（自动加载，供分析参考）\n" + memory_context
        if strategy_summary:
            system_content += "\n\n## 可用短线战法一览\n" + strategy_summary
        self.messages = [{"role": "system", "content": system_content}]

    def chat(self, user_input: str) -> str:
        """处理用户输入，返回 Agent 回复（非流式）"""
        self._track_session_info(user_input)
        self.messages.append({"role": "user", "content": user_input})

        for round_num in range(self.max_rounds):
            if self.verbose:
                print(f"\n[Round {round_num + 1}] 调用 LLM...")

            response = self.client.chat(self.messages, tools=TOOL_DEFINITIONS)
            self.messages.append(self.client.format_assistant_message(response))

            if response["stop_reason"] == "stop" or not response["tool_calls"]:
                return response["content"]

            self._execute_tools(response)

        return "分析轮次已达上限，请简化问题后重试。"

    def _prepare_user_input(self, user_input: str):
        """公共预处理：追踪会话信息、追加用户消息、重置轮内状态"""
        self._track_session_info(user_input)
        self.messages.append({"role": "user", "content": user_input})
        # 每轮用户输入重置工具追踪状态（跨 ReAct 轮次保持）
        self._tool_call_history.clear()
        self._consecutive_tool_errors = 0
        self._truncated_tools.clear()

    def _process_stream_response(self, response: dict):
        """公共后处理：将 LLM 响应追加到消息历史，判断是否结束"""
        if response is None:
            return True
        self.messages.append(self.client.format_assistant_message(response))
        return response["stop_reason"] == "stop" or not response["tool_calls"]

    def _yield_tool_execution(self, tc: dict, result: str):
        """公共工具执行结果 yield（含去重/错误检测）"""
        args_str = json.dumps(tc["arguments"], ensure_ascii=False)
        tool_start = f"{tc['name']}({args_str})"
        tool_result = result[:300] if len(result) > 300 else result
        self._track_tool_result(tc["name"], tc["arguments"], result)
        self.messages.append(self.client.format_tool_result_message(tc["id"], tc["name"], result))
        return tool_start, tool_result

    def chat_stream(self, user_input: str):
        """流式处理用户输入，yield (type, text) 元组。

        type: "thinking" / "content" / "tool_start" / "tool_result" / "round" / "done"
        """
        self._prepare_user_input(user_input)

        for round_num in range(self.max_rounds):
            yield ("round", round_num + 1)

            if self.verbose:
                print(f"\n[Round {round_num + 1}] 调用 LLM...")

            for chunk in self.client.chat_stream(self.messages, tools=TOOL_DEFINITIONS):
                if chunk.type == "thinking":
                    yield ("thinking", chunk.text)
                elif chunk.type == "content":
                    yield ("content", chunk.text)

            response = self.client.last_stream_response
            is_done = self._process_stream_response(response)

            if is_done:
                yield ("done", "")
                return

            for tc in response["tool_calls"]:
                result = self._run_tool(tc["name"], tc["arguments"])
                tool_start, tool_result = self._yield_tool_execution(tc, result)
                yield ("tool_start", tool_start)
                yield ("tool_result", tool_result)

            self._append_pending_system_notes()

        yield ("done", "")

    async def chat_stream_async(self, user_input: str):
        """异步流式处理用户输入，yield (type, text) 元组。

        type: "thinking" / "content" / "tool_start" / "tool_result" / "round" / "done"
        """
        self._prepare_user_input(user_input)

        for round_num in range(self.max_rounds):
            yield ("round", round_num + 1)

            if self.verbose:
                print(f"\n[Round {round_num + 1}] 调用 LLM...")

            async for chunk in self.client.chat_stream_async(self.messages, tools=TOOL_DEFINITIONS):
                if chunk.type == "thinking":
                    yield ("thinking", chunk.text)
                elif chunk.type == "content":
                    yield ("content", chunk.text)

            response = self.client.last_stream_response
            is_done = self._process_stream_response(response)

            if is_done:
                yield ("done", "")
                return

            for tc in response["tool_calls"]:
                result = await asyncio.to_thread(self._run_tool, tc["name"], tc["arguments"])
                tool_start, tool_result = self._yield_tool_execution(tc, result)
                yield ("tool_start", tool_start)
                yield ("tool_result", tool_result)

            self._append_pending_system_notes()

        yield ("done", "")

    async def chat_stream_json(self, user_input: str):
        """Web 端专用：异步流式输出 JSON 事件，适配 WebSocket 协议。

        yield 格式：
            {"type": "thinking", "delta": "..."}
            {"type": "content", "delta": "..."}
            {"type": "tool_call", "name": "...", "args": {...}}
            {"type": "tool_result", "name": "...", "data": "..."}
            {"type": "done", "rounds": N, "tools": M}
        """
        self._prepare_user_input(user_input)
        total_tools = 0

        for round_num in range(self.max_rounds):
            if self.verbose:
                print(f"\n[Round {round_num + 1}] 调用 LLM...")

            async for chunk in self.client.chat_stream_async(self.messages, tools=TOOL_DEFINITIONS):
                if chunk.type == "thinking":
                    yield {"type": "thinking", "delta": chunk.text}
                elif chunk.type == "content":
                    yield {"type": "content", "delta": chunk.text}

            response = self.client.last_stream_response
            is_done = self._process_stream_response(response)

            if is_done:
                yield {"type": "done", "rounds": round_num + 1, "tools": total_tools}
                return

            for tc in response["tool_calls"]:
                json.dumps(tc["arguments"], ensure_ascii=False)
                yield {"type": "tool_call", "name": tc["name"], "args": tc["arguments"]}

                result = await asyncio.to_thread(self._run_tool, tc["name"], tc["arguments"])
                self._track_tool_result(tc["name"], tc["arguments"], result)
                self.messages.append(self.client.format_tool_result_message(tc["id"], tc["name"], result))
                total_tools += 1

                tool_result_preview = result[:500] if len(result) > 500 else result
                yield {"type": "tool_result", "name": tc["name"], "data": tool_result_preview}

            self._append_pending_system_notes()

        yield {"type": "done", "rounds": self.max_rounds, "tools": total_tools}

    def _run_tool(self, tool_name: str, tool_args: dict) -> str:
        """执行单个工具，结果超长时智能截断以防上下文溢出"""
        if tool_name in TOOL_DISPATCH:
            try:
                result = TOOL_DISPATCH[tool_name](**tool_args)
                max_chars = getattr(_config, "MAX_TOOL_RESULT_CHARS", 8000)
                if len(result) > max_chars:
                    result = self._smart_truncate(result, max_chars, tool_name)
                self._check_memory_update(tool_name, result)
                return result
            except Exception as e:
                return json.dumps({"error": str(e), "tool": tool_name}, ensure_ascii=False)
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    def _smart_truncate(self, result: str, max_chars: int, tool_name: str) -> str:
        """智能截断工具结果：尝试保留合法JSON结构"""
        # 尝试 JSON 感知截断
        try:
            data = json.loads(result)
            # 找到主要的数据列表字段（kline, results, patterns 等）
            truncated = False
            for key in ("kline", "results", "patterns", "data", "news", "memories"):
                if isinstance(data.get(key), list) and len(data[key]) > 3:
                    original_len = len(data[key])
                    # 逐步缩减直到满足大小限制
                    for keep_ratio in [0.7, 0.5, 0.3, 0.2]:
                        test_data = dict(data)
                        test_data[key] = data[key][: max(3, int(original_len * keep_ratio))]
                        test_data["_truncated"] = True
                        test_data["_truncated_hint"] = (
                            f"数据已截断：原始{original_len}条，当前{len(test_data[key])}条。"
                            f"如需完整数据请缩小查询范围（如减少天数）"
                        )
                        test_str = json.dumps(test_data, ensure_ascii=False, default=str)
                        if len(test_str) <= max_chars:
                            return test_str
                    # 都不行，至少保留3条
                    data[key] = data[key][:3]
                    truncated = True
                    break

            if truncated:
                data["_truncated"] = True
                data["_truncated_hint"] = "数据已严重截断，仅保留部分记录。请缩小查询范围。"
                result = json.dumps(data, ensure_ascii=False, default=str)
                if len(result) <= max_chars:
                    return result
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

        # 兜底：在最后一个换行符处截断，保持可读
        truncated = result[:max_chars]
        last_newline = truncated.rfind("\n")
        if last_newline > max_chars * 0.8:
            truncated = truncated[:last_newline]
        return truncated + f"\n...[结果过长已截断，原始长度{len(result)}字符，请缩小查询范围]"

    @staticmethod
    def _is_error_result(result: str) -> bool:
        """检测工具返回是否为错误"""
        try:
            data = json.loads(result)
            return isinstance(data, dict) and "error" in data
        except (json.JSONDecodeError, TypeError):
            return False

    @staticmethod
    def _is_truncated_result(result: str) -> bool:
        """检测工具返回是否被截断"""
        try:
            data = json.loads(result)
            return isinstance(data, dict) and data.get("_truncated") is True
        except (json.JSONDecodeError, TypeError):
            # 非JSON结果中包含截断标记
            return "结果过长已截断" in result or "[_truncated]" in result

    def _check_memory_update(self, tool_name: str, tool_result: str):
        """记忆保存后注入提醒，让 LLM 知道新记忆已生效"""
        if tool_name == "save_memory":
            try:
                result = json.loads(tool_result)
                if result.get("status") == "saved":
                    mem_type = result.get("memory_type", "")
                    note = f"[系统提示] 新记忆已保存 (类型: {mem_type})。在后续分析中可以引用此记忆。"
                    self._pending_system_notes.append(note)
                    # learning 类型触发完整上下文刷新
                    if mem_type == "learning":
                        self.refresh_memory_context()
            except (json.JSONDecodeError, KeyError):
                pass

    def _append_pending_system_notes(self):
        """在工具结果消息之后追加系统提示，保持 tool_calls 消息顺序合法。"""
        if not self._pending_system_notes:
            return

        for note in self._pending_system_notes:
            self.messages.append({"role": "system", "content": note})
        self._pending_system_notes.clear()

    def refresh_memory_context(self):
        """重新加载记忆上下文到 system message"""
        memory_context = self.memory.get_context_block("", max_chars=2000)
        from deeppulse.agent.prompts import SYSTEM_PROMPT
        from deeppulse.agent.strategy_loader import get_strategy_loader

        strategy_summary = get_strategy_loader().get_all_content_summary(max_chars=2000)
        system_content = SYSTEM_PROMPT
        if memory_context:
            system_content += "\n\n## 历史记忆（自动加载，供分析参考）\n" + memory_context
        if strategy_summary:
            system_content += "\n\n## 可用短线战法一览\n" + strategy_summary
        self.messages[0]["content"] = system_content

    def _track_tool_result(self, tool_name: str, tool_args: dict, result: str):
        """追踪工具调用结果：去重检测、错误/截断注入 system note"""
        args_sig = json.dumps(tool_args, sort_keys=True, ensure_ascii=False)
        call_sig = (tool_name, args_sig)

        # 去重检测
        recent_calls = self._tool_call_history[-getattr(_config, "TOOL_REPEAT_DETECTION_WINDOW", 5) :]
        if call_sig in recent_calls:
            self._pending_system_notes.append(
                f"[系统提示] 工具 `{tool_name}` 使用相同参数刚刚调用过，结果相同。"
                f"请勿重复调用，直接使用已有结果。如果数据被截断，请缩小查询范围（如减少 days 参数）而非重试。"
            )
        self._tool_call_history.append(call_sig)

        # 错误/截断检测
        is_error = self._is_error_result(result)
        is_truncated = self._is_truncated_result(result)

        if is_error:
            self._consecutive_tool_errors += 1
            self._pending_system_notes.append(
                f"[系统提示] 工具 `{tool_name}` 调用失败：{result[:200]}。"
                f"请**不要编造**该工具应返回的数据。可尝试其他工具获取信息，或直接告知用户数据获取失败。"
            )
        elif is_truncated:
            self._consecutive_tool_errors = 0
            self._truncated_tools.add(tool_name)
            self._pending_system_notes.append(
                f"[系统提示] 工具 `{tool_name}` 返回数据被截断，当前数据不完整。"
                f"分析时请注明数据范围有限。如需完整数据，请用更小的参数重新查询（如减少天数）。"
            )
        else:
            self._consecutive_tool_errors = 0

        if self._consecutive_tool_errors >= 3:
            self._pending_system_notes.append(
                "[系统提示] ⚠️ 连续多次工具调用异常，当前分析环境受限。"
                "请停止尝试工具调用，直接基于已有数据给出分析，或告知用户当前无法完成分析。"
                "**严禁编造数据**。"
            )
            self._consecutive_tool_errors = 0

    def _execute_tools(self, response: dict):
        """执行工具调用并更新消息历史"""
        if self.verbose:
            for tc in response["tool_calls"]:
                args_str = json.dumps(tc["arguments"], ensure_ascii=False)
                print(f"  Tool: {tc['name']}({args_str})")

        for tc in response["tool_calls"]:
            result = self._run_tool(tc["name"], tc["arguments"])
            self._track_tool_result(tc["name"], tc["arguments"], result)

            if self.verbose:
                display = result[:200] + "..." if len(result) > 200 else result
                print(f"  Result: {display}")

            self.messages.append(self.client.format_tool_result_message(tc["id"], tc["name"], result))

        self._append_pending_system_notes()

    def on_session_end(self):
        """会话结束时保存摘要和上下文"""
        if not self.messages or len(self.messages) <= 1:
            return

        # 运行记忆维护：衰减遗忘 + LLM整合
        try:
            self.memory.decay_and_forget()
            # LLM 驱动的记忆整合（替代原来的规则压缩）
            self.memory.compress_memories_with_llm(self.client)
        except Exception:
            pass

        # 提取用户画像
        try:
            if self._user_messages:
                signals = self.memory.user_profile.extract_from_conversation(self._user_messages)
                for sig in signals:
                    self.memory.user_profile.update_profile(sig["key"], sig["value"], sig["confidence"])
        except Exception:
            pass

        # 提取会话中讨论的股票
        stocks = list(self._session_stocks)

        # 生成简短摘要
        summary = self._generate_session_summary()

        # 保存会话
        self.memory.end_session(
            session_id=self.session_id,
            summary=summary,
            topics=list(self._session_topics),
            stocks=stocks,
            message_count=len(self.messages),
        )

    def _generate_session_summary(self) -> str:
        """从对话历史中提取会话摘要"""
        # 收集用户消息和助手的关键回复
        user_msgs = []
        for msg in self.messages:
            if msg["role"] == "user" and isinstance(msg["content"], str):
                user_msgs.append(msg["content"][:100])

        if not user_msgs:
            return ""

        # 简单摘要：用户的前几个问题
        topics = "；".join(user_msgs[:3])
        stocks_str = ",".join(list(self._session_stocks)[:5]) if self._session_stocks else ""
        summary = f"讨论了：{topics}"
        if stocks_str:
            summary = f"涉及股票[{stocks_str}] " + summary

        return summary[:300]

    def _track_session_info(self, user_input: str):
        """从用户输入中提取股票代码和主题"""
        # 保存用户消息用于画像提取
        self._user_messages.append(user_input)

        # 提取6位股票代码
        codes = re.findall(r"\b[036]\d{5}\b", user_input)
        self._session_stocks.update(codes)

        # 提取可能的股票名称（简单匹配）
        stock_names = re.findall(r"[一-鿿]{2,6}(?:银行|证券|保险|集团|科技|电子|医药|能源|电力|股份)", user_input)
        self._session_stocks.update(stock_names[:3])

        # 提取主题关键词
        topic_keywords = re.findall(r"(?:短线|中线|长线|趋势|突破|支撑|压力|放量|缩量|金叉|死叉|超买|超卖)", user_input)
        self._session_topics.update(topic_keywords[:3])

        # 学习信号检测（规则层）
        signals = self.memory.detect_learning_signals(user_input)
        if signals:
            self._pending_system_notes.append(
                f"[系统提示] 检测到用户{'纠错' if signals[0]['type'] == '纠错' else '教学'}信号，"
                f"请仔细分析用户意图并调用 record_learning 保存学习记录。"
            )

    def reset(self):
        """重置对话历史（先保存会话摘要）"""
        self.on_session_end()
        # 生成新的 session_id
        self.session_id = str(uuid.uuid4())
        self.memory.start_session(self.session_id)
        self._session_stocks.clear()
        self._session_topics.clear()
        self._pending_system_notes.clear()
        self._tool_call_history.clear()
        self._consecutive_tool_errors = 0
        self._truncated_tools.clear()
        # 重新加载记忆上下文
        memory_context = self.memory.get_context_block("", max_chars=2000)
        system_content = SYSTEM_PROMPT
        if memory_context:
            system_content += "\n\n## 历史记忆（自动加载，供分析参考）\n" + memory_context
        self.messages = [{"role": "system", "content": system_content}]
