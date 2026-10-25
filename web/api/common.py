"""API 层公共工具：JSON 安全序列化、SSE 帧、错误响应"""

import json
import math

from fastapi.responses import JSONResponse


def nan_safe(obj):
    """递归把 NaN/Inf 浮点换成 None（JSON 规范不接受非有限浮点）。

    pandas fetchdf 的 NULL → NaN 会一路走到序列化层；Starlette 的 JSONResponse
    以 allow_nan=False 直接抛 ValueError，SSE 帧里的 NaN 字面量浏览器 JSON.parse 也拒。
    numpy 标量经 .item() 归一为 Python 原生值。
    """
    if isinstance(obj, dict):
        return {k: nan_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [nan_safe(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return nan_safe(item())
        except Exception:  # noqa: BLE001 - 非标量（如 DataFrame）走 default=str 兜底
            return obj
    return obj


def enc(obj):
    """JSON 安全编码（NaN/Inf → null；pandas.Timestamp 等一律 default=str）"""
    return json.loads(json.dumps(nan_safe(obj), ensure_ascii=False, default=str))


def sse_frame(event: str, data) -> str:
    """SSE 帧：`event: <type>\\ndata: <json>\\n\\n`（NaN/Inf → null，前端 JSON.parse 不炸）"""
    return f"event: {event}\ndata: {json.dumps(nan_safe(data), ensure_ascii=False, default=str)}\n\n"


class SafeJSONResponse(JSONResponse):
    """JSON 响应兜底：序列化前 NaN/Inf → null（忘走 enc 的端点也不 500）"""

    def render(self, content) -> bytes:
        return super().render(nan_safe(content))


def error_response(detail: str, status_code: int = 400, **extra) -> JSONResponse:
    """统一错误形状：{"detail": 中文原因, "candidates"?: [...]}"""
    body = {"detail": detail}
    body.update(extra)
    return SafeJSONResponse(body, status_code=status_code)


def parse_tool_start(text: str) -> tuple[str, dict]:
    """解析 tool_start 文本 `name({"k": v})` → (name, args)。

    工具执行完才到达此事件；括号内为 JSON，解析失败时保留原文。
    """
    raw = text or ""
    idx = raw.find("(")
    if idx < 0:
        return raw.strip(), {}
    name = raw[:idx].strip()
    body = raw[idx + 1 :]
    if body.endswith(")"):
        body = body[:-1]
    try:
        args = json.loads(body)
        if not isinstance(args, dict):
            args = {"_raw": args}
    except (json.JSONDecodeError, ValueError):
        args = {"_raw": body}
    return name, args
