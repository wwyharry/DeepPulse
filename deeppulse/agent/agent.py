"""Agent 核心 - ReAct 循环、工具调度、流式输出、记忆系统、战法库"""

import asyncio
import json
import re
import uuid

from deeppulse import config as _config
from deeppulse.agent.client import LLMClient
from deeppulse.agent.memory import MemoryManager
from deeppulse.agent.prompts import build_system_prompt
from deeppulse.agent.reasoning import build_dynamic_system_prompt, detect_market_state
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

        # 构建动态 system prompt（基础提示 + 市场状态/用户画像 + 记忆上下文 + 战法列表）
        self.messages = [{"role": "system", "content": self._build_system_content()}]

    def _build_system_content(self) -> str:
        """组装完整 system prompt

        结构：基础提示 → 动态推理链（市场状态 + 用户画像）→ 历史记忆 → 战法摘要。
        市场状态/用户画像获取失败时静默降级，不影响 Agent 可用性。
        """
        try:
            user_profile = json.loads(self.memory.user_profile.get_profile()).get("profile") or {}
        except Exception:
            user_profile = {}

        system_content = build_dynamic_system_prompt(
            build_system_prompt(),
            market_state=detect_market_state(),
            user_profile=user_profile if isinstance(user_profile, dict) else {},
        )

        memory_context = self.memory.get_context_block("", max_chars=2000)
        if memory_context:
            system_content += "\n\n## 历史记忆（自动加载，供分析参考）\n" + memory_context
        strategy_summary = get_strategy_loader().get_all_content_summary(max_chars=2000)
        if strategy_summary:
            system_content += "\n\n## 可用短线战法一览\n" + strategy_summary
        return system_content

    def chat(self, user_input: str) -> str:
        """处理用户输入，返回 Agent 回复（非流式）"""
        self._prepare_user_input(user_input)

        try:
            for round_num in range(self.max_rounds):
                if self.verbose:
                    print(f"\n[Round {round_num + 1}] 调用 LLM...")

                response = self.client.chat(self.messages, tools=TOOL_DEFINITIONS)
                self.messages.append(self.client.format_assistant_message(response))

                if response["stop_reason"] == "stop" or not response["tool_calls"]:
                    self._pending_system_notes.clear()
                    return response["content"]

                self._execute_tools(response)

            # 轮次耗尽：最终回复必须写入历史，否则下一轮模型不知道自己说过什么
            msg = "分析轮次已达上限，请简化问题后重试。"
            self.messages.append(
                self.client.format_assistant_message({"content": msg, "tool_calls": None, "stop_reason": "stop"})
            )
            self._pending_system_notes.clear()
            return msg
        except Exception:
            # 流中断占位：保证历史中用户消息后必有 assistant 消息，再抛出原异常
            if self.messages and self.messages[-1]["role"] != "assistant":
                self.messages.append(
                    self.client.format_assistant_message(
                        {"content": "[分析中断]", "tool_calls": None, "stop_reason": "stop"}
                    )
                )
            raise

    def _prepare_user_input(self, user_input: str):
        """公共预处理：追踪会话信息、追加用户消息、重置轮内状态"""
        # 上一轮残留提示作废（轮内作用域），避免过时指令泄漏到不相关提问
        self._pending_system_notes.clear()
        self._track_session_info(user_input)
        self.messages.append({"role": "user", "content": user_input})
        # 学习信号等轮首提示立即下发，本轮是否调用工具都能生效
        self._append_pending_system_notes()
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

        try:
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
                    self._pending_system_notes.clear()
                    yield ("done", "")
                    return

                for tc in response["tool_calls"]:
                    result = self._run_tool(tc["name"], tc["arguments"])
                    tool_start, tool_result = self._yield_tool_execution(tc, result)
                    yield ("tool_start", tool_start)
                    yield ("tool_result", tool_result)

                self._append_pending_system_notes()

            # 轮次耗尽：写入历史并下发给用户，避免历史以 tool_result 结尾
            msg = "分析轮次已达上限，请简化问题后重试。"
            self.messages.append(
                self.client.format_assistant_message({"content": msg, "tool_calls": None, "stop_reason": "stop"})
            )
            self._pending_system_notes.clear()
            yield ("content", msg)
            yield ("done", "")
        except Exception:
            if self.messages and self.messages[-1]["role"] != "assistant":
                self.messages.append(
                    self.client.format_assistant_message(
                        {"content": "[分析中断]", "tool_calls": None, "stop_reason": "stop"}
                    )
                )
            raise

    async def chat_stream_async(self, user_input: str):
        """异步流式处理用户输入，yield (type, text) 元组。

        type: "thinking" / "content" / "tool_start" / "tool_result" / "round" / "done"
        """
        self._prepare_user_input(user_input)

        try:
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
                    self._pending_system_notes.clear()
                    yield ("done", "")
                    return

                for tc in response["tool_calls"]:
                    result = await asyncio.to_thread(self._run_tool, tc["name"], tc["arguments"])
                    tool_start, tool_result = self._yield_tool_execution(tc, result)
                    yield ("tool_start", tool_start)
                    yield ("tool_result", tool_result)

                self._append_pending_system_notes()

            # 轮次耗尽：写入历史并下发给用户，避免历史以 tool_result 结尾
            msg = "分析轮次已达上限，请简化问题后重试。"
            self.messages.append(
                self.client.format_assistant_message({"content": msg, "tool_calls": None, "stop_reason": "stop"})
            )
            self._pending_system_notes.clear()
            yield ("content", msg)
            yield ("done", "")
        except Exception:
            if self.messages and self.messages[-1]["role"] != "assistant":
                self.messages.append(
                    self.client.format_assistant_message(
                        {"content": "[分析中断]", "tool_calls": None, "stop_reason": "stop"}
                    )
                )
            raise

    def _run_tool(self, tool_name: str, tool_args: dict) -> str:
        """执行单个工具，结果超长时智能截断以防上下文溢出"""
        # 工具参数 JSON 解析失败：显式报错让 LLM 重试，不静默用默认参数执行
        if isinstance(tool_args, dict) and "__parse_error__" in tool_args:
            return json.dumps(
                {
                    "error": "tool arguments parse failed",
                    "tool": tool_name,
                    "detail": tool_args.get("__parse_error__"),
                    "raw": tool_args.get("__raw__", ""),
                    "hint": "上一次工具调用参数不是合法 JSON（可能被 max_tokens 截断）。请用完整合法的 JSON 参数重新调用该工具。",
                },
                ensure_ascii=False,
            )
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
        """智能截断工具结果：保持合法 JSON 结构，绝不把真实数据替换成 error"""
        try:
            data = json.loads(result)
        except Exception:
            data = None  # 非 JSON 文本走硬截断兜底

        if isinstance(data, (dict, list)):
            shrunk = self._shrink_json(data, max_chars)
            if isinstance(data, dict):
                shrunk["_truncated"] = True
                shrunk.setdefault("_truncated_hint", "数据已严重截断，仅保留部分记录。请缩小查询范围。")
            else:
                # 顶层 list 包一层 dict 信封，保留 _truncated 语义
                shrunk = {
                    "_truncated": True,
                    "_truncated_hint": "数据已严重截断，仅保留部分记录。请缩小查询范围。",
                    "items": shrunk if isinstance(shrunk, list) else [shrunk],
                }
            try:
                out = json.dumps(shrunk, ensure_ascii=False, default=str)
                if len(out) <= max_chars:
                    return out
            except Exception:
                pass
            # 无法安全装入限制：返回合法 error 信封，绝不硬切 JSON 文本
            return json.dumps(
                {"error": "结果过大且无法安全截断，请缩小查询范围", "tool": tool_name},
                ensure_ascii=False,
            )

        if isinstance(data, str):
            # JSON 字符串值：截断后仍以合法 JSON 字符串返回
            keep = max(0, max_chars - 60)
            return json.dumps(data[:keep] + "...[结果过长已截断]", ensure_ascii=False)

        # 兜底：非 JSON 文本在最后一个换行符处截断，保持可读
        truncated = result[:max_chars]
        last_newline = truncated.rfind("\n")
        if last_newline > max_chars * 0.8:
            truncated = truncated[:last_newline]
        return truncated + f"\n...[结果过长已截断，原始长度{len(result)}字符，请缩小查询范围]"

    def _shrink_json(self, data, max_chars: int):
        """在保持 JSON 结构的前提下收缩到预算内（给 _truncated 标记预留空间）。"""
        budget = max(200, max_chars - 400)

        def fits(obj) -> bool:
            try:
                return len(json.dumps(obj, ensure_ascii=False, default=str)) <= budget
            except Exception:
                return False

        if fits(data):
            return data

        if isinstance(data, dict):
            # 1) 优先削减识别键下的大列表（kline, results, patterns 等）
            for key in ("kline", "results", "patterns", "data", "news", "memories"):
                if isinstance(data.get(key), list) and len(data[key]) > 3:
                    original_len = len(data[key])
                    for keep_ratio in [0.7, 0.5, 0.3, 0.2]:
                        test_data = dict(data)
                        test_data[key] = data[key][: max(3, int(original_len * keep_ratio))]
                        if fits(test_data):
                            test_data["_truncated"] = True
                            test_data["_truncated_hint"] = (
                                f"数据已截断：原始{original_len}条，当前{len(test_data[key])}条。"
                                f"如需完整数据请缩小查询范围（如减少天数）"
                            )
                            return test_data
                    data = dict(data)
                    data[key] = data[key][:3]
                    break

            if fits(data):
                data["_truncated"] = True
                data["_truncated_hint"] = "数据已严重截断，仅保留部分记录。请缩小查询范围。"
                return data

            # 2) 识别键不够时：从最大的嵌套列表/长字符串入手收缩
            self._shrink_largest(data, fits)
            return data

        if isinstance(data, list):
            original_len = len(data)
            for keep_ratio in [0.7, 0.5, 0.3, 0.2]:
                kept = data[: max(3, int(original_len * keep_ratio))]
                if fits(kept):
                    return kept
            return data[:3]

        return data

    @classmethod
    def _collect_shrinkables(cls, node, out: list):
        """收集可收缩节点 (kind, parent, key, size)：大列表与超长字符串。"""
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, list) and len(v) > 3:
                    out.append(("list", node, k, len(v)))
                elif isinstance(v, str) and len(v) > 1000:
                    out.append(("str", node, k, len(v)))
                elif isinstance(v, (dict, list)):
                    cls._collect_shrinkables(v, out)
        elif isinstance(node, list):
            for item in node:
                cls._collect_shrinkables(item, out)

    @classmethod
    def _shrink_largest(cls, data, fits):
        """从最大的列表/长字符串入手收缩（保留 ≥3 条 / ≥100 字符），直到装入预算。"""
        candidates: list = []
        cls._collect_shrinkables(data, candidates)
        candidates.sort(key=lambda c: c[3], reverse=True)
        for kind, parent, key, _size in candidates:
            if fits(data):
                return
            if kind == "list":
                original_len = len(parent[key])
                for ratio in (0.7, 0.5, 0.3, 0.2):
                    parent[key] = parent[key][: max(3, int(original_len * ratio))]
                    if fits(data):
                        return
                parent[key] = parent[key][:3]
            else:
                original = parent[key]
                for ratio in (0.5, 0.3, 0.2):
                    parent[key] = original[: max(100, int(len(original) * ratio))]
                    if fits(data):
                        return
                parent[key] = original[:100]

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
        self.messages[0]["content"] = self._build_system_content()

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

        # 提取6位股票代码（汉字属 \w，\b 在中文相邻时失效，改用否定环视）
        codes = re.findall(r"(?<![0-9A-Za-z])[036]\d{5}(?![0-9A-Za-z])", user_input)
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
        # 重新构建 system prompt（含动态推理链、记忆与战法摘要）
        self.messages = [{"role": "system", "content": self._build_system_content()}]
