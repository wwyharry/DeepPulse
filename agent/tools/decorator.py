import inspect
import functools
import typing

TOOL_DEFINITIONS: list = []
TOOL_DISPATCH: dict = {}

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _param_entry_to_prop(entry: str | tuple) -> dict:
    """解析 param_desc 中的单个条目

    - str: 只有描述
    - tuple: (描述, 额外字段dict)
    """
    prop = {}
    if isinstance(entry, str):
        prop["description"] = entry
    elif isinstance(entry, tuple):
        prop["description"] = entry[0]
        if len(entry) > 1 and isinstance(entry[1], dict):
            prop.update(entry[1])
    return prop


def _get_json_type(annotation):
    """从类型注解推断 JSON schema type，处理 Literal->enum"""
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        args = typing.get_args(annotation)
        first_type = type(args[0]) if args else str
        if all(isinstance(a, type(first_type)) for a in args):
            return _TYPE_MAP.get(first_type, "string"), {"enum": list(args)}
        return _TYPE_MAP.get(first_type, "string"), {}
    return _TYPE_MAP.get(annotation, "string"), {}


def tool(description="", name=None, param_desc: list[str | tuple] = None, type="function"):
    """注册一个函数为 LLM tool，自动生成 OpenAI tool calling schema。

    用法:
        # 最简（仅描述）
        @tool("搜索股票")
        def search_stock(keyword: str) -> str: ...

        # 带 param_desc
        @tool("保存投资预测", param_desc=[
            "6位股票代码",
            ("预测方向", {"enum": ["bullish", "bearish", "neutral"]}),
            "股票名称（可选）",
        ])
        def save_prediction(stock_code: str, direction: str, stock_name: str = "") -> str: ...

    Args:
        description: 工具功能描述，传给 LLM 的 tool description。
        name: 工具名，默认用函数名，必须唯一。
        param_desc: 按参数位置排列的描述或额外字段。
            每项可以是 str（纯描述）或 tuple(描述, 额外字段 dict)。
            不传的字段自动从函数签名推导（type / default / required）。
        type: OpenAI tool calling 的 type，默认 "function"。
    """
    def decorator(func):
        tool_name = name or func.__name__
        sig = inspect.signature(func)
        properties, required = {}, []

        for i, (pname, param) in enumerate(sig.parameters.items()):
            annotation = param.annotation if param.annotation != inspect.Parameter.empty else str
            json_type, extra = _get_json_type(annotation)

            prop = {"type": json_type, **extra}

            if param_desc and i < len(param_desc):
                prop.update(_param_entry_to_prop(param_desc[i]))

            if param.default != inspect.Parameter.empty:
                if param.default is not None:
                    prop["default"] = param.default
            else:
                required.append(pname)

            properties[pname] = prop

        TOOL_DEFINITIONS.append({
            "type": type,
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
        TOOL_DISPATCH[tool_name] = func
        print(tool_name)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper

    return decorator
