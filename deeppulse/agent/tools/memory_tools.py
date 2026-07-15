"""记忆系统工具 - 记忆CRUD、预测跟踪、用户画像、学习记录、知识图谱"""

import json

from deeppulse.agent.tools._shared import get_memory


def save_memory(
    content: str,
    memory_type: str = "insight",
    keywords: str = "",
    tags: str = "",
    importance: float = 0.5,
    learned_what: str = "",
    learned_why: str = "",
    apply_when: str = "",
) -> str:
    """保存一条长期记忆，供未来分析时参考。完成股票分析后应保存结论，用户表达偏好时也应保存。检测到用户教学/纠错时，必须保存为learning类型。"""
    mem = get_memory()
    return mem.save_memory(
        content=content,
        memory_type=memory_type,
        keywords=keywords,
        tags=tags,
        importance=float(importance),
        learned_what=learned_what,
        learned_why=learned_why,
        apply_when=apply_when,
    )


def search_memory(query: str, memory_type: str = "", top_k: int = 5) -> str:
    """搜索长期记忆。在开始新的分析前，先搜索是否有相关的历史记忆可以帮助分析。"""
    mem = get_memory()
    return mem.search_memories(query, memory_type=memory_type, top_k=int(top_k))


def update_memory(memory_id: str, content: str = "", importance: float = -1, tags: str = "") -> str:
    """更新已有记忆的内容、重要度或标签。"""
    mem = get_memory()
    return mem.update_memory(memory_id, content=content, importance=float(importance), tags=tags)


def delete_memory(memory_id: str) -> str:
    """删除一条记忆。"""
    mem = get_memory()
    return mem.delete_memory(memory_id)


def list_memories(memory_type: str = "", limit: int = 20) -> str:
    """列出已保存的长期记忆。"""
    mem = get_memory()
    return mem.list_memories(memory_type=memory_type, limit=int(limit))


def save_session_context(stock_code: str = "", topic: str = "", notes: str = "") -> str:
    """保存当前会话上下文，用于跨会话的分析延续。"""
    content = f"分析股票: {stock_code}" if stock_code else ""
    if topic:
        content += f" | 主题: {topic}"
    if notes:
        content += f" | 笔记: {notes}"
    if not content:
        return json.dumps({"error": "未提供有效内容"}, ensure_ascii=False)
    mem = get_memory()
    return mem.save_memory(content=content, memory_type="context", keywords=stock_code, tags="会话上下文")


def save_prediction(
    stock_code: str,
    direction: str,
    stock_name: str = "",
    prediction_type: str = "direction",
    target_price: float = 0,
    stop_loss: float = 0,
    timeframe_days: int = 5,
    reasoning: str = "",
    confidence: float = 0.5,
) -> str:
    """保存一条投资预测，用于后续验证准确率并从对错中学习。在给出分析结论时应调用此工具。"""
    mem = get_memory()
    memory_ids = mem._session_new_memory_ids[-3:] if mem._session_new_memory_ids else []
    return mem.prediction_tracker.save_prediction(
        stock_code=stock_code,
        stock_name=stock_name,
        prediction_type=prediction_type,
        direction=direction,
        target_price=float(target_price),
        stop_loss=float(stop_loss),
        timeframe_days=int(timeframe_days),
        reasoning=reasoning,
        confidence=float(confidence),
        memory_ids=memory_ids,
    )


def check_predictions(stock_code: str = "") -> str:
    """检查待验证的预测。分析某只股票时会自动调用，也可手动查看所有到期预测。"""
    mem = get_memory()
    return mem.prediction_tracker.check_predictions(stock_code if stock_code else None)


def verify_prediction(prediction_id: str, actual_price: float, actual_return_pct: float = 0) -> str:
    """验证预测结果，更新准确率并自动调整关联记忆的重要性。"""
    mem = get_memory()
    return mem.prediction_tracker.verify_prediction(prediction_id, float(actual_price), float(actual_return_pct))


def prediction_stats() -> str:
    """查看预测准确率统计，包括总验证数、正确/错误数、按方向分类统计。"""
    mem = get_memory()
    return mem.prediction_tracker.get_accuracy_stats()


def get_user_profile() -> str:
    """获取用户交易画像，包括交易风格、风险偏好、关注板块等。"""
    mem = get_memory()
    return mem.user_profile.get_profile()


def update_user_profile(key: str, value: str, confidence: float = 0.5) -> str:
    """更新用户画像信息。"""
    mem = get_memory()
    return mem.user_profile.update_profile(key, value, float(confidence))


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
    content = f"学到: {learned_what}"
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
        importance=float(importance),
        learned_what=learned_what,
        learned_why=learned_why,
        apply_when=apply_when,
    )


def query_knowledge(entity_name: str = "", entity_type: str = "", relation_type: str = "") -> str:
    """查询知识图谱中的实体和关系。用于查找技术指标、战法、股票之间的关联关系。"""
    mem = get_memory()
    return mem.knowledge_graph.query_related(
        entity_name if entity_name else None,
        entity_type if entity_type else None,
        relation_type if relation_type else None,
    )


def add_knowledge(
    source_name: str, source_type: str, target_name: str, target_type: str, relation_type: str, weight: float = 1.0
) -> str:
    """向知识图谱添加一条关系。当发现技术指标、战法、市场规律之间的关联时调用。"""
    mem = get_memory()
    source_id = mem.knowledge_graph.add_entity(source_type, source_name)
    target_id = mem.knowledge_graph.add_entity(target_type, target_name)
    rel_id = mem.knowledge_graph.add_relation(source_id, target_id, relation_type, float(weight))
    return json.dumps(
        {
            "status": "added",
            "source": f"{source_name}({source_type})",
            "relation": relation_type,
            "target": f"{target_name}({target_type})",
            "relation_id": rel_id,
        },
        ensure_ascii=False,
    )


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "保存一条长期记忆，供未来分析时参考。完成股票分析后应保存结论，用户表达偏好时也应保存。检测到用户教学/纠错时，必须保存为learning类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "记忆内容（分析结论、用户偏好、市场事实、用户教学等）",
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
                        "description": "重要度 0.0-1.0，越重要越不容易被遗忘。用户教学建议0.7-0.9",
                        "default": 0.5,
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
                    "top_k": {"type": "integer", "description": "返回条数，默认5", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "更新已有记忆的内容、重要度或标签。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "要更新的记忆ID"},
                    "content": {"type": "string", "description": "新内容（可选）", "default": ""},
                    "importance": {
                        "type": "number",
                        "description": "新的重要度（可选，-1表示保持原值）",
                        "default": -1,
                    },
                    "tags": {"type": "string", "description": "新的标签（可选，逗号分隔）", "default": ""},
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
            "description": "列出已保存的长期记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {"type": "string", "description": "按类型筛选（可选）", "default": ""},
                    "limit": {"type": "integer", "description": "最多返回条数，默认20", "default": 20},
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
            "description": "保存一条投资预测，用于后续验证准确率并从对错中学习。在给出分析结论时应调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "6位股票代码"},
                    "direction": {
                        "type": "string",
                        "description": "预测方向 - 'bullish'(看涨)/'bearish'(看跌)/'neutral'(中性)",
                    },
                    "stock_name": {"type": "string", "description": "股票名称（可选）", "default": ""},
                    "prediction_type": {
                        "type": "string",
                        "description": "预测类型 - 'direction'(方向)/'price_range'(价位)/'pattern'(形态)",
                        "default": "direction",
                    },
                    "target_price": {"type": "number", "description": "目标价位（可选）", "default": 0},
                    "stop_loss": {"type": "number", "description": "止损价位（可选）", "default": 0},
                    "timeframe_days": {"type": "integer", "description": "预测有效天数，默认5", "default": 5},
                    "reasoning": {"type": "string", "description": "预测理由（简述关键依据）", "default": ""},
                    "confidence": {"type": "number", "description": "置信度 0.0-1.0", "default": 0.5},
                },
                "required": ["stock_code", "direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_predictions",
            "description": "检查待验证的预测。分析某只股票时会自动调用，也可手动查看所有到期预测。",
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
            "description": "验证预测结果，更新准确率并自动调整关联记忆的重要性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prediction_id": {"type": "string", "description": "预测ID"},
                    "actual_price": {"type": "number", "description": "实际价格"},
                    "actual_return_pct": {
                        "type": "number",
                        "description": "实际收益率（百分比，如 5.2 表示涨5.2%）",
                        "default": 0,
                    },
                },
                "required": ["prediction_id", "actual_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prediction_stats",
            "description": "查看预测准确率统计，包括总验证数、正确/错误数、按方向分类统计。",
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
            "description": "更新用户画像信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "画像维度 - 'trading_style'(交易风格)/'risk_tolerance'(风险偏好)/'preferred_indicators'(偏好指标)/'watched_sectors'(关注板块)/'stop_loss_habit'(止损习惯)/'position_sizing'(仓位习惯)",
                    },
                    "value": {"type": "string", "description": "对应的值"},
                    "confidence": {"type": "number", "description": "置信度 0.0-1.0，默认0.5", "default": 0.5},
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
    {
        "type": "function",
        "function": {
            "name": "query_knowledge",
            "description": "查询知识图谱中的实体和关系。用于查找技术指标、战法、股票之间的关联关系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "实体名称（模糊匹配，可选）", "default": ""},
                    "entity_type": {
                        "type": "string",
                        "description": "实体类型 - 'stock'/'indicator'/'pattern'/'strategy'/'concept'/'rule'（可选）",
                        "default": "",
                    },
                    "relation_type": {
                        "type": "string",
                        "description": "关系类型 - 'triggers'/'requires'/'contradicts'/'supports'/'applies_to'/'user_prefers'（可选）",
                        "default": "",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_knowledge",
            "description": "向知识图谱添加一条关系。当发现技术指标、战法、市场规律之间的关联时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_name": {"type": "string", "description": "源实体名称（如 'RSI超卖'）"},
                    "source_type": {"type": "string", "description": "源实体类型（如 'indicator'）"},
                    "target_name": {"type": "string", "description": "目标实体名称（如 '低吸战法'）"},
                    "target_type": {"type": "string", "description": "目标实体类型（如 'strategy'）"},
                    "relation_type": {
                        "type": "string",
                        "description": "关系类型 - 'triggers'/'requires'/'contradicts'/'supports'/'applies_to'/'user_prefers'",
                    },
                    "weight": {"type": "number", "description": "权重 0.0-1.0，默认1.0", "default": 1.0},
                },
                "required": ["source_name", "source_type", "target_name", "target_type", "relation_type"],
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
    "query_knowledge": query_knowledge,
    "add_knowledge": add_knowledge,
}
