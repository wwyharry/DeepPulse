"""记忆系统工具 - 记忆CRUD、预测跟踪、用户画像、学习记录"""

import functools
import json

from deeppulse.agent.memory.constants import DEFAULT_HALF_LIFE
from deeppulse.agent.memory.profile import VALID_PROFILE_KEYS
from deeppulse.agent.tools._shared import get_current_session_id, get_memory, parse_positive_int

_CONTENT_SOFT_MAX = 2000
_LEARN_FIELD_SOFT_MAX = 500


def _db_guard(fn):
    """底层库锁冲突（多进程同库）转结构化错误，别把堆栈甩给模型"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - 工具边界兜底
            msg = str(e)
            if "lock" in msg.lower() or "IOException" in msg:
                return json.dumps({"error": "数据库繁忙（可能有另一进程在写库），请稍后重试"}, ensure_ascii=False)
            raise

    return wrapper


def _validate_importance(importance, required: bool = False):
    """importance 校验：None（缺省）放行，越界/非数返回错误描述"""
    if importance is None:
        if required:
            raise ValueError("importance 不能为空")
        return None
    try:
        v = float(importance)
    except (TypeError, ValueError):
        raise ValueError(f"importance 必须为数字，收到 {importance!r}") from None
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"importance 必须在 0.0-1.0 之间，收到 {v}")
    return v


def _validate_confidence(confidence):
    try:
        v = float(confidence)
    except (TypeError, ValueError):
        raise ValueError(f"confidence 必须为数字，收到 {confidence!r}") from None
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"confidence 必须在 0.0-1.0 之间，收到 {v}")
    return v


def _soft_trim(value: str, limit: int) -> str:
    return (value or "")[:limit]


@_db_guard
def save_memory(
    content: str,
    memory_type: str = "insight",
    keywords: str = "",
    tags: str = "",
    importance: float = None,
    learned_what: str = "",
    learned_why: str = "",
    apply_when: str = "",
) -> str:
    """保存一条长期记忆，供未来分析时参考。完成股票分析后应保存结论，用户表达偏好时也应保存。检测到用户教学/纠错时，必须保存为learning类型。"""
    content = (content or "").strip()
    if not content:
        return json.dumps({"error": "content 不能为空"}, ensure_ascii=False)
    if len(content) > _CONTENT_SOFT_MAX:
        return json.dumps(
            {"error": f"content 超长（{len(content)} 字符，上限 {_CONTENT_SOFT_MAX}），请精炼后保存"},
            ensure_ascii=False,
        )
    if memory_type not in DEFAULT_HALF_LIFE:
        return json.dumps(
            {"error": f"未知 memory_type: {memory_type!r}，允许值: {sorted(DEFAULT_HALF_LIFE)}"}, ensure_ascii=False
        )
    try:
        imp = _validate_importance(importance)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    mem = get_memory()
    return mem.save_memory(
        content=content,
        memory_type=memory_type,
        keywords=keywords or "",
        tags=tags or "",
        importance=imp,
        source_session_id=get_current_session_id(),
        learned_what=_soft_trim(learned_what, _LEARN_FIELD_SOFT_MAX),
        learned_why=_soft_trim(learned_why, _LEARN_FIELD_SOFT_MAX),
        apply_when=_soft_trim(apply_when, _LEARN_FIELD_SOFT_MAX),
    )


@_db_guard
def search_memory(query: str, memory_type: str = "", top_k: int = 5) -> str:
    """搜索长期记忆。在开始新的分析前，先搜索是否有相关的历史记忆可以帮助分析。"""
    if not (query or "").strip():
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
    top_k = parse_positive_int(top_k, "top_k", default=5)
    if memory_type and memory_type not in DEFAULT_HALF_LIFE:
        return json.dumps(
            {"error": f"未知 memory_type: {memory_type!r}，允许值: {sorted(DEFAULT_HALF_LIFE)}"}, ensure_ascii=False
        )
    mem = get_memory()
    return mem.search_memories(query, memory_type=memory_type or "", top_k=top_k)


@_db_guard
def update_memory(
    memory_id: str,
    content: str = "",
    importance: float = -1,
    tags: str = None,
    keywords: str = None,
    learned_what: str = None,
    learned_why: str = None,
    apply_when: str = None,
) -> str:
    """更新已有记忆的内容、重要度、关键词或标签。tags 传空串=清空标签，不传=保持不变。"""
    if not (memory_id or "").strip():
        return json.dumps({"error": "memory_id 不能为空"}, ensure_ascii=False)
    if content and len(content) > _CONTENT_SOFT_MAX:
        return json.dumps(
            {"error": f"content 超长（{len(content)} 字符，上限 {_CONTENT_SOFT_MAX}）"}, ensure_ascii=False
        )
    imp = -1.0
    if importance is not None and float(importance) != -1:
        try:
            imp = _validate_importance(importance)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    mem = get_memory()
    return mem.update_memory(
        memory_id,
        content=content or "",
        importance=float(imp),
        tags=tags,
        keywords=keywords,
        learned_what=learned_what,
        learned_why=learned_why,
        apply_when=apply_when,
    )


@_db_guard
def delete_memory(memory_id: str) -> str:
    """删除一条记忆。"""
    if not (memory_id or "").strip():
        return json.dumps({"error": "memory_id 不能为空"}, ensure_ascii=False)
    mem = get_memory()
    return mem.delete_memory(memory_id)


@_db_guard
def list_memories(memory_type: str = "", limit: int = 20) -> str:
    """列出已保存的长期记忆。"""
    limit = parse_positive_int(limit, "limit", default=20)
    if memory_type and memory_type not in DEFAULT_HALF_LIFE:
        return json.dumps(
            {"error": f"未知 memory_type: {memory_type!r}，允许值: {sorted(DEFAULT_HALF_LIFE)}"}, ensure_ascii=False
        )
    mem = get_memory()
    return mem.list_memories(memory_type=memory_type or "", limit=limit)


@_db_guard
def save_session_context(stock_code: str = "", topic: str = "", notes: str = "") -> str:
    """保存当前会话上下文，用于跨会话的分析延续。"""
    parts = []
    if (stock_code or "").strip():
        parts.append(f"分析股票: {stock_code.strip()}")
    if (topic or "").strip():
        parts.append(f"主题: {topic.strip()}")
    if (notes or "").strip():
        parts.append(f"笔记: {notes.strip()}")
    if not parts:
        return json.dumps({"error": "未提供有效内容"}, ensure_ascii=False)
    content = " | ".join(parts)
    mem = get_memory()
    return mem.save_memory(
        content=content,
        memory_type="context",
        keywords=(stock_code or "").strip(),
        tags="会话上下文",
        source_session_id=get_current_session_id(),
    )


@_db_guard
def save_prediction(
    stock_code: str,
    direction: str,
    stock_name: str = "",
    prediction_type: str = "direction",
    target_price: float = None,
    stop_loss: float = None,
    entry_price: float = None,
    timeframe_days: int = 5,
    reasoning: str = "",
    confidence: float = 0.5,
    memory_ids: str = "",
) -> str:
    """保存一条投资预测，用于后续验证准确率并从对错中学习。在给出分析结论时应调用此工具。

    未设置目标/止损价请不传（存 null，不会伪造成 0 元）。memory_ids 可显式关联本结论依据的记忆。
    """
    if not (stock_code or "").strip():
        return json.dumps({"error": "stock_code 不能为空"}, ensure_ascii=False)
    timeframe_days = parse_positive_int(timeframe_days, "timeframe_days", default=5)
    try:
        conf = _validate_confidence(confidence)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    mem = get_memory()
    # 关联记忆：显式传入优先；缺省只取当前会话新增（不摸共享单例的私有累积列表，防跨会话污染）
    if (memory_ids or "").strip():
        rel_ids = [m.strip() for m in memory_ids.split(",") if m.strip()]
    else:
        rel_ids = mem.get_session_memory_ids(get_current_session_id(), limit=3)
    return mem.prediction_tracker.save_prediction(
        stock_code=stock_code.strip(),
        stock_name=stock_name or "",
        prediction_type=prediction_type or "direction",
        direction=direction,
        target_price=target_price,
        stop_loss=stop_loss,
        timeframe_days=timeframe_days,
        reasoning=reasoning or "",
        confidence=conf,
        memory_ids=rel_ids,
        entry_price=entry_price,
    )


@_db_guard
def check_predictions(stock_code: str = "") -> str:
    """检查待验证的预测。分析某只股票时会自动调用，也可手动查看所有到期预测。"""
    mem = get_memory()
    return mem.prediction_tracker.check_predictions(stock_code if (stock_code or "").strip() else None)


@_db_guard
def verify_prediction(
    prediction_id: str,
    actual_price: float = None,
    actual_return_pct: float = None,
    force: bool = False,
) -> str:
    """验证预测结果，更新准确率并自动调整关联记忆的重要性。一次性判定，结果不可覆盖。

    判定规则：设了目标/止损价时按价差判定（需 actual_price）；否则按收益率判定（±3% 带：
    看涨收益>+3% 正确、<-3% 错误，区间内部分；看跌对称）。仅目标价无止损时，反向偏离目标
    15% 以上记错误。actual_price 为正的真实成交价，未知请改传 actual_return_pct（勿传 0）。
    未到期预测需 force=true 才能提前验证。
    """
    if not (prediction_id or "").strip():
        return json.dumps({"error": "prediction_id 不能为空"}, ensure_ascii=False)
    mem = get_memory()
    return mem.prediction_tracker.verify_prediction(
        prediction_id.strip(),
        actual_price=actual_price,
        actual_return_pct=None if actual_return_pct is None else float(actual_return_pct),
        force=bool(force),
    )


@_db_guard
def prediction_stats() -> str:
    """查看预测准确率统计，包括总验证数、正确/错误数、双口径准确率与按方向分类统计。"""
    mem = get_memory()
    return mem.prediction_tracker.get_accuracy_stats()


@_db_guard
def get_user_profile() -> str:
    """获取用户交易画像，包括交易风格、风险偏好、关注板块等。"""
    mem = get_memory()
    return mem.user_profile.get_profile()


@_db_guard
def update_user_profile(key: str, value: str, confidence: float = 0.5, source_memory_ids: str = "") -> str:
    """更新用户画像信息。低置信度的新值不会推翻高置信度的旧共识。"""
    if key not in VALID_PROFILE_KEYS:
        return json.dumps({"error": f"未知画像键: {key!r}，允许值: {sorted(VALID_PROFILE_KEYS)}"}, ensure_ascii=False)
    try:
        conf = _validate_confidence(confidence)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    src_ids = [m.strip() for m in (source_memory_ids or "").split(",") if m.strip()]
    mem = get_memory()
    return mem.user_profile.update_profile(key, value, conf, source_memory_ids=src_ids or None)


@_db_guard
def record_learning(
    learned_what: str,
    learned_why: str,
    apply_when: str,
    category: str = "user_correction",
    related_indicators: str = "",
    related_stocks: str = "",
    importance: float = 0.8,
) -> str:
    """显式记录一条学习知识。当检测到用户教学或纠错时调用此工具。"""
    if not (learned_what or "").strip():
        return json.dumps({"error": "learned_what 不能为空"}, ensure_ascii=False)
    try:
        imp = _validate_importance(importance)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    content = f"学到: {learned_what.strip()}"
    if related_indicators:
        content += f" | 相关指标: {related_indicators}"
    if related_stocks:
        content += f" | 相关股票: {related_stocks}"

    keywords = ",".join(filter(None, [related_indicators, related_stocks, category]))
    tags = f"学习,{category}"

    mem = get_memory()
    return mem.save_memory(
        content=content,
        memory_type="learning",
        keywords=keywords,
        tags=tags,
        importance=imp,
        source_session_id=get_current_session_id(),
        learned_what=_soft_trim(learned_what, _LEARN_FIELD_SOFT_MAX),
        learned_why=_soft_trim(learned_why, _LEARN_FIELD_SOFT_MAX),
        apply_when=_soft_trim(apply_when, _LEARN_FIELD_SOFT_MAX),
    )


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "保存一条长期记忆，供未来分析时参考。完成股票分析后应保存结论，用户表达偏好时也应保存。检测到用户教学/纠错时，必须保存为learning类型。完全相同内容不会重复堆积（自动去重刷新）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "记忆内容（分析结论、用户偏好、市场事实、用户教学等，不超过2000字）",
                    },
                    "memory_type": {
                        "type": "string",
                        "description": "记忆类型: preference(偏好), insight(分析结论), fact(事实), context(上下文), summary(摘要), learning(用户教学/纠错)",
                        "default": "insight",
                    },
                    "keywords": {
                        "type": "string",
                        "description": "逗号分隔的关键词，如 '600519,贵州茅台,支撑位'",
                        "default": "",
                    },
                    "tags": {"type": "string", "description": "逗号分隔的标签，如 '技术分析,短线'", "default": ""},
                    "importance": {
                        "type": "number",
                        "description": "重要度 0.0-1.0，越重要越不容易被遗忘。不传时按类型默认（learning 0.7，其余 0.5）",
                    },
                    "learned_what": {
                        "type": "string",
                        "description": "学到了什么（用于learning类型，简明扼要）",
                        "default": "",
                    },
                    "learned_why": {"type": "string", "description": "为什么这个知识重要", "default": ""},
                    "apply_when": {"type": "string", "description": "什么情况下应该应用这个知识", "default": ""},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "搜索长期记忆。在开始新的分析前，先搜索是否有相关的历史记忆可以帮助分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，可以包含股票代码、名称或主题词"},
                    "memory_type": {
                        "type": "string",
                        "description": "按类型筛选（可选）: preference/insight/fact/context/summary/learning",
                        "default": "",
                    },
                    "top_k": {"type": "integer", "description": "返回条数，默认5，最大50", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "更新已有记忆的内容、重要度、关键词或标签。tags 传空串=清空标签，不传=保持不变。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "要更新的记忆ID"},
                    "content": {
                        "type": "string",
                        "description": "新内容（可选；原关键词会保留并与新提取合并）",
                        "default": "",
                    },
                    "importance": {
                        "type": "number",
                        "description": "新的重要度 0.0-1.0（可选，-1表示保持原值）",
                        "default": -1,
                    },
                    "tags": {"type": "string", "description": "新的标签（可选，逗号分隔；空串=清空；不传=不变）"},
                    "keywords": {
                        "type": "string",
                        "description": "新的关键词（可选，逗号分隔；不传=保留原词并自动补充）",
                    },
                    "learned_what": {"type": "string", "description": "学到了什么（可选，不传=不变）"},
                    "learned_why": {"type": "string", "description": "为什么重要（可选，不传=不变）"},
                    "apply_when": {"type": "string", "description": "应用时机（可选，不传=不变）"},
                },
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "删除一条记忆。",
            "parameters": {
                "type": "object",
                "properties": {"memory_id": {"type": "string", "description": "要删除的记忆ID"}},
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "列出已保存的长期记忆。total 为库内总数，returned 为本次返回条数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {"type": "string", "description": "按类型筛选（可选）", "default": ""},
                    "limit": {
                        "type": "integer",
                        "description": "最多返回条数，默认20，最大200",
                        "default": 20,
                        "minimum": 1,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_session_context",
            "description": "保存当前会话上下文，用于跨会话的分析延续。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "当前正在分析的股票代码", "default": ""},
                    "topic": {"type": "string", "description": "当前分析主题", "default": ""},
                    "notes": {"type": "string", "description": "临时笔记", "default": ""},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_prediction",
            "description": "保存一条投资预测，用于后续验证准确率并从对错中学习。在给出分析结论时应调用此工具。目标/止损/建仓价未设置时不传即可（存 null，不会记成 0 元）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "6位股票代码"},
                    "direction": {
                        "type": "string",
                        "description": "预测方向 - 'bullish'(看涨)/'bearish'(看跌)/'neutral'(中性)，仅此三值",
                    },
                    "stock_name": {"type": "string", "description": "股票名称（可选）", "default": ""},
                    "prediction_type": {
                        "type": "string",
                        "description": "预测类型 - 'direction'(方向)/'price_range'(价位)/'pattern'(形态)",
                        "default": "direction",
                    },
                    "target_price": {"type": "number", "description": "目标价位（可选，未设置不传）"},
                    "stop_loss": {"type": "number", "description": "止损价位（可选，未设置不传）"},
                    "entry_price": {"type": "number", "description": "建仓/基准价位（可选，用于事后复核收益率）"},
                    "timeframe_days": {
                        "type": "integer",
                        "description": "预测有效天数 1-365，默认5",
                        "default": 5,
                    },
                    "reasoning": {"type": "string", "description": "预测理由（简述关键依据）", "default": ""},
                    "confidence": {"type": "number", "description": "置信度 0.0-1.0", "default": 0.5},
                    "memory_ids": {
                        "type": "string",
                        "description": "逗号分隔的关联记忆ID（可选；不传时自动关联本会话最近保存的记忆）",
                        "default": "",
                    },
                },
                "required": ["stock_code", "direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_predictions",
            "description": "检查待验证的预测。分析某只股票时会自动调用，也可手动查看所有到期预测。含 check_after_date 与 due 标记，未到期预测不应提前验证。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {
                        "type": "string",
                        "description": "股票代码（可选，不填则检查所有到期预测）",
                        "default": "",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_prediction",
            "description": "验证预测结果，更新准确率并自动调整关联记忆的重要性。一次性判定，结果不可覆盖。设了目标/止损价时按价差判定（需 actual_price）；否则按收益率 ±3% 带判定（看涨>+3% 正确、<-3% 错误，看跌对称）；仅目标价无止损时反向偏离目标 15% 以上记错误。未到期预测需 force=true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prediction_id": {"type": "string", "description": "预测ID"},
                    "actual_price": {
                        "type": "number",
                        "description": "实际价格（正数；未知时勿传 0，改传 actual_return_pct）",
                    },
                    "actual_return_pct": {
                        "type": "number",
                        "description": "实际收益率（百分比，如 5.2 表示涨5.2%）；设了目标/止损价的预测需提供 actual_price",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "未到期预测强制提前验证（默认 false，会污染准确率，慎用）",
                        "default": False,
                    },
                },
                "required": ["prediction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prediction_stats",
            "description": "查看预测准确率统计，包括总验证数、正确/错误数、双口径准确率（全验证/已决出胜负）与按方向分类统计。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": "获取用户交易画像，包括交易风格、风险偏好、关注板块等。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": "更新用户画像信息。低置信度的新值不会推翻高置信度的旧共识。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "画像维度 - 'trading_style'(交易风格)/'risk_tolerance'(风险偏好)/'preferred_indicators'(偏好指标)/'watched_sectors'(关注板块)/'watched_stocks'(关注股票)/'stop_loss_habit'(止损习惯)/'position_sizing'(仓位习惯)/'profit_target'(止盈目标)/'trading_frequency'(交易频率)",
                    },
                    "value": {"type": "string", "description": "对应的值"},
                    "confidence": {"type": "number", "description": "置信度 0.0-1.0，默认0.5", "default": 0.5},
                    "source_memory_ids": {
                        "type": "string",
                        "description": "逗号分隔的证据记忆ID（可选）",
                        "default": "",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_learning",
            "description": "显式记录一条学习知识。当检测到用户教学或纠错时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "learned_what": {"type": "string", "description": "学到了什么（简明扼要）"},
                    "learned_why": {"type": "string", "description": "为什么这个知识重要"},
                    "apply_when": {"type": "string", "description": "什么情况下应该应用这个知识"},
                    "category": {
                        "type": "string",
                        "description": "学习类别 - 'indicator_usage'(指标用法)/'risk_management'(风险管理)/'market_pattern'(市场规律)/'user_correction'(用户纠错)/'trading_technique'(交易技巧)",
                        "default": "user_correction",
                    },
                    "related_indicators": {"type": "string", "description": "相关技术指标（逗号分隔）", "default": ""},
                    "related_stocks": {"type": "string", "description": "相关股票代码（逗号分隔）", "default": ""},
                    "importance": {"type": "number", "description": "重要度 0.0-1.0，默认0.8", "default": 0.8},
                },
                "required": ["learned_what", "learned_why", "apply_when"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "save_memory": save_memory,
    "search_memory": search_memory,
    "update_memory": update_memory,
    "delete_memory": delete_memory,
    "list_memories": list_memories,
    "save_session_context": save_session_context,
    "save_prediction": save_prediction,
    "check_predictions": check_predictions,
    "verify_prediction": verify_prediction,
    "prediction_stats": prediction_stats,
    "get_user_profile": get_user_profile,
    "update_user_profile": update_user_profile,
    "record_learning": record_learning,
}
