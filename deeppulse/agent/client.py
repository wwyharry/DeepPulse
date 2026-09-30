"""统一 LLM 客户端 - 支持 OpenAI 和 Anthropic 两种协议，支持流式输出和异步"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _is_placeholder_api_key(key_val) -> bool:
    """明显占位 key 判定（与 setup_config.py 口径一致）。

    占位 = 空值/纯空白 / "${}" 空占位（dollar+花括号）/ "sk-your" 示例前缀。
    未展开的环境变量形式（dollar+花括号+变量名，如 "${ENV_VAR}"）不算占位，
    保留视为已配置，交给 load_setting 的环境变量展开逻辑解析。
    """
    if key_val is None:
        return True
    s = str(key_val)
    if not s.strip():
        return True
    if s.startswith("${") and s.endswith("}") and not s[2:-1].strip():
        return True
    return s.startswith("sk-your")


def load_setting() -> dict:
    """加载 setting.json 配置"""
    setting_path = Path(__file__).parent.parent.parent / "setting.json"
    with open(setting_path, encoding="utf-8") as f:
        setting = json.load(f)

    llm = setting["llm"]
    key_val = llm["api_key"]
    # 载入时拒绝明显占位 key（空/空白/"${}"/"sk-your" 示例值），提示配置 setting.json
    if _is_placeholder_api_key(key_val):
        raise ValueError(
            'API Key 未配置（空值/空白/"${}"占位/"sk-your"示例值）。请编辑 setting.json:\n'
            '  1. 直接填入 API Key: "api_key": "sk-..."（真实 key）\n'
            '  2. 或引用环境变量: "api_key": "${YOUR_ENV_VAR}"'
        )

    # 解析 api_key: 支持 ${ENV_VAR} 引用环境变量，或直接写 key
    key_val = str(key_val)
    if key_val.startswith("${") and key_val.endswith("}"):
        inner = key_val[2:-1].strip()
        # 先尝试作为环境变量名查找
        env_val = os.environ.get(inner)
        if env_val:
            llm["api_key"] = env_val
        elif inner.startswith("sk-"):
            # 内容本身是 API key，直接使用
            llm["api_key"] = inner
        else:
            raise ValueError(
                f"环境变量 {inner} 未设置。请:\n"
                f"  1. 设置环境变量: set {inner}=your-key (Windows) 或 export {inner}=your-key (Linux/Mac)\n"
                f'  2. 或直接在 setting.json 中填入 API Key: "api_key": "sk-..."（真实 key）'
            )

    # 展开后再次拦截占位值（如环境变量里存了示例 key）
    if _is_placeholder_api_key(llm["api_key"]):
        raise ValueError(
            'API Key 解析后仍为占位值（空值/空白/"${}"占位/"sk-your"示例值）。请编辑 setting.json 配置真实 API Key'
        )

    return setting


@dataclass
class StreamChunk:
    """流式输出的一个片段"""

    type: str  # "thinking" / "content" / "tool_calls" / "done"
    text: str = ""
    tool_calls: list = field(default_factory=list)


def _parse_tool_arguments(raw) -> dict:
    """解析工具调用参数。

    空/None/纯空白视为零参调用，返回 {}；
    非法 JSON 或非对象时返回带 __parse_error__ 标记的 dict，
    由上层拦截提示重试，而不是抛异常崩溃或静默替换成 {}。
    """
    if raw is None:
        return {}
    if not isinstance(raw, str):
        raw = str(raw)
    if not raw.strip():
        return {}
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return {"__parse_error__": f"工具参数 JSON 不完整/非法: {e}", "__raw__": raw[-200:]}
    if not isinstance(args, dict):
        return {"__parse_error__": "tool arguments must be object", "__raw__": raw[-200:]}
    return args


class LLMClient:
    """统一 LLM 客户端，根据 protocol 自动选择 OpenAI 或 Anthropic SDK"""

    def __init__(self, setting: dict = None):
        if setting is None:
            setting = load_setting()
        self.setting = setting
        self.llm_config = setting["llm"]
        self.protocol = self.llm_config["protocol"]
        self._init_client()

    def _init_client(self):
        if self.protocol == "openai":
            from openai import AsyncOpenAI, OpenAI

            self.client = OpenAI(
                api_key=self.llm_config["api_key"],
                base_url=self.llm_config["base_url"],
            )
            self.async_client = AsyncOpenAI(
                api_key=self.llm_config["api_key"],
                base_url=self.llm_config["base_url"],
            )
        elif self.protocol == "anthropic":
            from anthropic import Anthropic, AsyncAnthropic

            self.client = Anthropic(api_key=self.llm_config["api_key"])
            self.async_client = AsyncAnthropic(api_key=self.llm_config["api_key"])
        else:
            raise ValueError(f"不支持的协议: {self.protocol}")

    def chat(self, messages: list, tools: list = None) -> dict:
        """发送对话请求，返回统一格式的响应

        Returns:
            {
                "content": str,           # 文本内容
                "tool_calls": list | None, # 工具调用列表
                "stop_reason": str         # stop / tool_calls
            }
        """
        if self.protocol == "openai":
            return self._chat_openai(messages, tools)
        else:
            return self._chat_anthropic(messages, tools)

    def chat_stream(self, messages: list, tools: list = None):
        """流式对话，yield StreamChunk。完成后 self.last_stream_response 包含完整结果。"""
        self.last_stream_response = None
        gen = (
            self._stream_openai(messages, tools)
            if self.protocol == "openai"
            else self._stream_anthropic(messages, tools)
        )
        # 手动消费生成器，捕获 return 值
        try:
            while True:
                try:
                    chunk = next(gen)
                    yield chunk
                except StopIteration as e:
                    # 内部生成器通过 self.last_stream_response 设置结果，
                    # 仅在内部生成器显式 return 了值时才覆盖
                    if e.value is not None:
                        self.last_stream_response = e.value
                    break
        finally:
            gen.close()

    async def chat_stream_async(self, messages: list, tools: list = None):
        """异步流式对话，yield StreamChunk。完成后 self.last_stream_response 包含完整结果。"""
        self.last_stream_response = None
        if self.protocol == "openai":
            async for chunk in self._stream_openai_async(messages, tools):
                if chunk.type == "done":
                    break
                yield chunk
        else:
            async for chunk in self._stream_anthropic_async(messages, tools):
                if chunk.type == "done":
                    break
                yield chunk

    async def chat_stream_async_full(self, messages: list, tools: list = None):
        """异步流式对话，yield 所有事件包括 tool_calls 和 done。"""
        self.last_stream_response = None
        if self.protocol == "openai":
            async for chunk in self._stream_openai_async(messages, tools):
                yield chunk
        else:
            async for chunk in self._stream_anthropic_async(messages, tools):
                yield chunk

    def _chat_openai(self, messages, tools) -> dict:
        kwargs = {
            "model": self.llm_config["model"],
            "messages": messages,
            "max_tokens": self.llm_config["max_tokens"],
            "temperature": self.llm_config["temperature"],
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        result = {
            "content": msg.content or "",
            "tool_calls": None,
            "stop_reason": "stop",
            "reasoning_content": getattr(msg, "reasoning_content", None),
        }

        if msg.tool_calls:
            result["tool_calls"] = []
            for tc in msg.tool_calls:
                result["tool_calls"].append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": _parse_tool_arguments(tc.function.arguments),
                    }
                )
            result["stop_reason"] = "tool_calls"

        return result

    def _chat_anthropic(self, messages, tools) -> dict:
        # Anthropic 协议转换：按序合并全部 system 消息（主提示在前，中途注释在后），
        # 不能只保留最后一条，否则工具报错等中途注释会顶掉整个系统提示词
        system_parts = []
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                api_messages.append(m)

        kwargs = {
            "model": self.llm_config["model"],
            "messages": api_messages,
            "max_tokens": self.llm_config["max_tokens"],
            "temperature": self.llm_config["temperature"],
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            kwargs["tools"] = self._convert_tools_to_anthropic(tools)

        resp = self.client.messages.create(**kwargs)

        result = {
            "content": "",
            "tool_calls": None,
            "stop_reason": "stop",
        }

        for block in resp.content:
            if block.type == "text":
                result["content"] += block.text
            elif block.type == "tool_use":
                if result["tool_calls"] is None:
                    result["tool_calls"] = []
                result["tool_calls"].append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    }
                )
                result["stop_reason"] = "tool_calls"

        return result

    def _build_openai_stream_kwargs(self, messages, tools) -> dict:
        """构造 OpenAI 流式调用参数"""
        kwargs = {
            "model": self.llm_config["model"],
            "messages": messages,
            "max_tokens": self.llm_config["max_tokens"],
            "temperature": self.llm_config["temperature"],
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    def _process_openai_chunk(self, chunk, state: dict):
        """处理单个 OpenAI 流式 chunk，更新 state，返回 list[StreamChunk]（可能为空）"""
        if not chunk.choices:
            return []
        delta = chunk.choices[0].delta
        finish = chunk.choices[0].finish_reason

        chunks = []
        rc = getattr(delta, "reasoning_content", None)
        if rc:
            state["reasoning_buf"] += rc
            chunks.append(StreamChunk(type="thinking", text=rc))

        if delta.content:
            state["content_buf"] += delta.content
            chunks.append(StreamChunk(type="content", text=delta.content))

        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in state["tool_calls_buf"]:
                    state["tool_calls_buf"][idx] = {
                        "id": tc.id or "",
                        "name": tc.function.name if tc.function and tc.function.name else "",
                        "arguments": "",
                    }
                else:
                    if tc.id:
                        state["tool_calls_buf"][idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        state["tool_calls_buf"][idx]["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    state["tool_calls_buf"][idx]["arguments"] += tc.function.arguments

        state["done"] = finish in ("stop", "tool_calls")
        return chunks

    def _build_openai_result(self, state: dict) -> dict:
        """从累积 state 构造 OpenAI 最终结果"""
        result = {
            "content": state["content_buf"],
            "tool_calls": None,
            "stop_reason": "stop",
            "reasoning_content": state["reasoning_buf"] or None,
        }
        if state["tool_calls_buf"]:
            result["tool_calls"] = []
            result["stop_reason"] = "tool_calls"
            for idx in sorted(state["tool_calls_buf"].keys()):
                tc = state["tool_calls_buf"][idx]
                result["tool_calls"].append(
                    {"id": tc["id"], "name": tc["name"], "arguments": _parse_tool_arguments(tc["arguments"])}
                )
        return result

    def _stream_openai(self, messages, tools):
        """OpenAI 协议流式输出"""
        kwargs = self._build_openai_stream_kwargs(messages, tools)
        stream = self.client.chat.completions.create(**kwargs)
        state = {"reasoning_buf": "", "content_buf": "", "tool_calls_buf": {}, "done": False}

        try:
            for chunk in stream:
                yield from self._process_openai_chunk(chunk, state)
                if state["done"]:
                    break
        finally:
            # 提前终止/异常时释放 HTTP 连接（close 幂等）
            stream.close()

        result = self._build_openai_result(state)
        self.last_stream_response = result
        yield StreamChunk(type="done")

    async def _stream_openai_async(self, messages, tools):
        """OpenAI 协议异步流式输出"""
        kwargs = self._build_openai_stream_kwargs(messages, tools)
        stream = await self.async_client.chat.completions.create(**kwargs)
        state = {"reasoning_buf": "", "content_buf": "", "tool_calls_buf": {}, "done": False}

        try:
            async for chunk in stream:
                for sc in self._process_openai_chunk(chunk, state):
                    yield sc
                if state["done"]:
                    break
        finally:
            # finally 中只可 await、不可再 yield
            await stream.close()

        result = self._build_openai_result(state)
        self.last_stream_response = result

        # 如果有工具调用，yield 工具调用事件
        if result.get("tool_calls"):
            yield StreamChunk(type="tool_calls", tool_calls=result["tool_calls"])

        yield StreamChunk(type="done")

    def _build_anthropic_stream_kwargs(self, messages, tools) -> dict:
        """构造 Anthropic 流式调用参数"""
        # 按序合并全部 system 消息（主提示在前，中途注释在后），
        # 不能只保留最后一条，否则中途注释会顶掉整个系统提示词
        system_parts = []
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                api_messages.append(m)

        kwargs = {
            "model": self.llm_config["model"],
            "messages": api_messages,
            "max_tokens": self.llm_config["max_tokens"],
            "temperature": self.llm_config["temperature"],
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            kwargs["tools"] = self._convert_tools_to_anthropic(tools)
        return kwargs

    def _process_anthropic_event(self, event, state: dict):
        """处理单个 Anthropic 流式事件，更新 state，返回 StreamChunk 或 None"""
        result_chunk = None
        if event.type == "content_block_start":
            if event.content_block.type == "tool_use":
                state["current_tool"] = {
                    "id": event.content_block.id,
                    "name": event.content_block.name,
                    "arguments": "",
                }
        elif event.type == "content_block_delta":
            if event.delta.type == "thinking_delta":
                result_chunk = StreamChunk(type="thinking", text=event.delta.thinking)
            elif event.delta.type == "text_delta":
                state["content_buf"] += event.delta.text
                result_chunk = StreamChunk(type="content", text=event.delta.text)
            elif event.delta.type == "input_json_delta":
                if state["current_tool"]:
                    state["current_tool"]["arguments"] += event.delta.partial_json
        elif event.type == "content_block_stop":
            if state["current_tool"]:
                state["tool_calls_buf"].append(state["current_tool"])
                state["current_tool"] = None
        return result_chunk

    def _build_anthropic_result(self, state: dict) -> dict:
        """从累积 state 构造 Anthropic 最终结果"""
        result = {
            "content": state["content_buf"],
            "tool_calls": None,
            "stop_reason": "stop",
        }
        if state["tool_calls_buf"]:
            result["tool_calls"] = []
            result["stop_reason"] = "tool_calls"
            for tc in state["tool_calls_buf"]:
                result["tool_calls"].append(
                    {"id": tc["id"], "name": tc["name"], "arguments": _parse_tool_arguments(tc["arguments"])}
                )
        return result

    def _stream_anthropic(self, messages, tools):
        """Anthropic 协议流式输出"""
        kwargs = self._build_anthropic_stream_kwargs(messages, tools)
        state = {"content_buf": "", "tool_calls_buf": [], "current_tool": None}

        with self.client.messages.stream(**kwargs) as stream:
            for event in stream:
                sc = self._process_anthropic_event(event, state)
                if sc:
                    yield sc

        result = self._build_anthropic_result(state)
        self.last_stream_response = result
        yield StreamChunk(type="done")

    async def _stream_anthropic_async(self, messages, tools):
        """Anthropic 协议异步流式输出"""
        kwargs = self._build_anthropic_stream_kwargs(messages, tools)
        state = {"content_buf": "", "tool_calls_buf": [], "current_tool": None}

        async with self.async_client.messages.stream(**kwargs) as stream:
            async for event in stream:
                sc = self._process_anthropic_event(event, state)
                if sc:
                    yield sc

        result = self._build_anthropic_result(state)
        self.last_stream_response = result
        yield StreamChunk(type="done")

    def _convert_tools_to_anthropic(self, tools) -> list:
        """将 OpenAI 格式的 tools 转换为 Anthropic 格式"""
        anthropic_tools = []
        for t in tools:
            func = t["function"]
            anthropic_tools.append(
                {
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"],
                }
            )
        return anthropic_tools

    def format_tool_result_message(self, tool_call_id: str, tool_name: str, result: str) -> dict:
        """构造工具结果消息"""
        if self.protocol == "openai":
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            }
        else:
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": result,
                    }
                ],
            }

    def format_assistant_message(self, response: dict) -> dict:
        """将响应转换为消息格式存入历史"""
        if self.protocol == "openai":
            msg = {"role": "assistant", "content": response["content"]}
            # DeepSeek 思维链模型需要回传 reasoning_content
            if response.get("reasoning_content"):
                msg["reasoning_content"] = response["reasoning_content"]
            if response["tool_calls"]:
                msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in response["tool_calls"]
                ]
            return msg
        else:
            # Anthropic 格式
            blocks = []
            if response["content"]:
                blocks.append({"type": "text", "text": response["content"]})
            if response["tool_calls"]:
                for tc in response["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc["arguments"],
                        }
                    )
            return {"role": "assistant", "content": blocks}
