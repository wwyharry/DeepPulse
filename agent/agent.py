"""Agent 核心 - ReAct 循环、工具调度、流式输出、记忆系统、战法库"""
import json
import re
import uuid
from agent.client import LLMClient, StreamChunk
from agent.tools import TOOL_DEFINITIONS, TOOL_DISPATCH
from agent.prompts import SYSTEM_PROMPT
from agent.memory import MemoryManager
from agent.strategy_loader import get_strategy_loader
import config as _config


class StockAgent:
    """股票短线分析 Agent"""

    def __init__(self, setting: dict = None, verbose: bool = None,
                 session_id: str = None):
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

    def chat_stream(self, user_input: str):
        """流式处理用户输入，yield (type, text) 元组。

        type: "thinking" / "content" / "tool_start" / "tool_result" / "round" / "done"
        """
        self._track_session_info(user_input)
        self.messages.append({"role": "user", "content": user_input})

        for round_num in range(self.max_rounds):
            yield ("round", round_num + 1)

            if self.verbose:
                print(f"\n[Round {round_num + 1}] 调用 LLM...")

            # 流式调用，逐 chunk yield
            for chunk in self.client.chat_stream(self.messages, tools=TOOL_DEFINITIONS):
                if chunk.type == "thinking":
                    yield ("thinking", chunk.text)
                elif chunk.type == "content":
                    yield ("content", chunk.text)

            # 流式完成后从 client 获取完整结构化结果
            response = self.client.last_stream_response

            self.messages.append(self.client.format_assistant_message(response))

            if response["stop_reason"] == "stop" or not response["tool_calls"]:
                yield ("done", "")
                return

            # 执行工具
            for tc in response["tool_calls"]:
                args_str = json.dumps(tc["arguments"], ensure_ascii=False)
                yield ("tool_start", f"{tc['name']}({args_str})")

                result = self._run_tool(tc["name"], tc["arguments"])
                yield ("tool_result", result[:300] if len(result) > 300 else result)

                self.messages.append(
                    self.client.format_tool_result_message(tc["id"], tc["name"], result)
                )

            self._append_pending_system_notes()

        yield ("done", "")

    def _drain_stream(self, gen, yield_fn):
        """消费流式生成器，返回最终结果"""
        response = None
        try:
            for chunk in gen:
                if yield_fn and chunk.type in ("thinking", "content"):
                    yield_fn(chunk)
                # done chunk 不需要转发
        except StopIteration as e:
            response = e.value
        return response

    def _run_tool(self, tool_name: str, tool_args: dict) -> str:
        """执行单个工具，结果超长时自动截断以防上下文溢出"""
        if tool_name in TOOL_DISPATCH:
            try:
                result = TOOL_DISPATCH[tool_name](**tool_args)
                max_chars = getattr(_config, 'MAX_TOOL_RESULT_CHARS', 8000)
                if len(result) > max_chars:
                    result = result[:max_chars] + f"\n...[结果过长已截断，原始长度{len(result)}字符，请缩小查询范围]"
                self._check_memory_update(tool_name, result)
                return result
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

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
        from agent.prompts import SYSTEM_PROMPT
        from agent.strategy_loader import get_strategy_loader
        strategy_summary = get_strategy_loader().get_all_content_summary(max_chars=2000)
        system_content = SYSTEM_PROMPT
        if memory_context:
            system_content += "\n\n## 历史记忆（自动加载，供分析参考）\n" + memory_context
        if strategy_summary:
            system_content += "\n\n## 可用短线战法一览\n" + strategy_summary
        self.messages[0]["content"] = system_content

    def _execute_tools(self, response: dict):
        """执行工具调用并更新消息历史"""
        if self.verbose:
            for tc in response["tool_calls"]:
                args_str = json.dumps(tc["arguments"], ensure_ascii=False)
                print(f"  Tool: {tc['name']}({args_str})")

        for tc in response["tool_calls"]:
            result = self._run_tool(tc["name"], tc["arguments"])

            if self.verbose:
                display = result[:200] + "..." if len(result) > 200 else result
                print(f"  Result: {display}")

            self.messages.append(
                self.client.format_tool_result_message(tc["id"], tc["name"], result)
            )

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
                    self.memory.user_profile.update_profile(
                        sig["key"], sig["value"], sig["confidence"]
                    )
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
        codes = re.findall(r'\b[036]\d{5}\b', user_input)
        self._session_stocks.update(codes)

        # 提取可能的股票名称（简单匹配）
        stock_names = re.findall(r'[一-鿿]{2,6}(?:银行|证券|保险|集团|科技|电子|医药|能源|电力|股份)',
                                  user_input)
        self._session_stocks.update(stock_names[:3])

        # 提取主题关键词
        topic_keywords = re.findall(r'(?:短线|中线|长线|趋势|突破|支撑|压力|放量|缩量|金叉|死叉|超买|超卖)',
                                     user_input)
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
        # 重新加载记忆上下文
        memory_context = self.memory.get_context_block("", max_chars=2000)
        system_content = SYSTEM_PROMPT
        if memory_context:
            system_content += "\n\n## 历史记忆（自动加载，供分析参考）\n" + memory_context
        self.messages = [{"role": "system", "content": system_content}]
