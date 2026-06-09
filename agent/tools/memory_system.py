from agent.tools.decorator import tool

from agent.memory import MemoryManager

try:
    from agent.client import load_setting as _load_setting

    _setting = _load_setting()
except Exception:
    _setting = None
_memory = MemoryManager(setting=_setting)
_memory.init_tables()

# ============ 记忆系统工具 ============


@tool(
    "保存一条长期记忆，供未来分析时参考。完成股票分析后应保存结论，用户表达偏好时也应保存。检测到用户教学/纠错时，必须保存为learning类型。",
    param_desc=[
            "记忆内容（分析结论、用户偏好、市场事实、用户教学等）",
            "记忆类型: preference(偏好), insight(分析结论), fact(事实), context(上下文), summary(摘要), learning(用户教学/纠错)",
            "逗号分隔的关键词，如 '600519,贵州茅台,支撑位'",
            "逗号分隔的标签，如 '技术分析,短线'",
            "重要度 0.0-1.0，越重要越不容易被遗忘。用户教学建议0.7-0.9",
            "学到了什么（用于learning类型，简明扼要）",
            "为什么这个知识重要",
            "什么情况下应该应用这个知识",
        ],
)
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
    """保存一条长期记忆，供未来分析时参考。

    Args:
        content: 记忆内容（分析结论、用户偏好、市场事实等）
        memory_type: 记忆类型 - 'preference'(用户偏好), 'insight'(分析结论), 'fact'(事实记录), 'context'(上下文线索), 'summary'(会话摘要), 'learning'(用户教学/纠错)
        keywords: 逗号分隔的关键词，用于检索（如 '600519,贵州茅台,支撑位'）
        tags: 逗号分隔的标签（如 '技术分析,短线,白酒'）
        importance: 重要度 0.0-1.0，默认0.5。越重要的记忆越不容易被遗忘
        learned_what: 学到了什么（用于learning类型，简明扼要）
        learned_why: 为什么重要（用于learning类型）
        apply_when: 什么情况下应用这个知识（用于learning类型）
    """
    return _memory.save_memory(
        content,
        memory_type,
        keywords,
        tags,
        importance,
        learned_what=learned_what,
        learned_why=learned_why,
        apply_when=apply_when,
    )


@tool(
    "搜索长期记忆。在开始新的分析前，先搜索是否有相关的历史记忆可以帮助分析。",
    param_desc=[
            "搜索关键词，可以包含股票代码、名称或主题词",
            "按类型筛选（可选）: preference/insight/fact/context/summary/learning",
            "返回条数，默认5",
        ],
)
def search_memory(query: str, memory_type: str = "", top_k: int = 5) -> str:
    """搜索长期记忆。在开始新的分析前，先搜索是否有相关的历史记忆可以帮助分析。

    Args:
        query: 搜索关键词，可以包含股票代码、名称或主题词
        memory_type: 按类型筛选（可选）: preference/insight/fact/context/summary
        top_k: 返回条数，默认5
    """
    return _memory.search_memories(query, memory_type, int(top_k))


@tool(
    "更新已有记忆的内容、重要度或标签。",
    param_desc=[
            "要更新的记忆ID",
            "新内容（可选）",
            "新的重要度（可选，-1表示保持原值）",
            "新的标签（可选，逗号分隔）",
        ],
)
def update_memory(memory_id: str, content: str = "", importance: float = -1, tags: str = "") -> str:
    """更新已有记忆的内容、重要度或标签。

    Args:
        memory_id: 要更新的记忆ID
        content: 新内容（可选，不填则保持原内容）
        importance: 新的重要度（可选，-1表示保持原值）
        tags: 新的标签（可选，逗号分隔）
    """
    return _memory.update_memory(memory_id, content, float(importance), tags)


@tool(
    "删除一条记忆。",
    param_desc=[
            "要删除的记忆ID",
        ],
)
def delete_memory(memory_id: str) -> str:
    """删除一条记忆（软删除，标记为归档）。

    Args:
        memory_id: 要删除的记忆ID
    """
    return _memory.delete_memory(memory_id)


@tool(
    "列出已保存的长期记忆。",
    param_desc=[
            "按类型筛选（可选）",
            "最多返回条数，默认20",
        ],
)
def list_memories(memory_type: str = "", limit: int = 20) -> str:
    """列出已保存的长期记忆。

    Args:
        memory_type: 按类型筛选（可选）
        limit: 最多返回条数，默认20
    """
    return _memory.list_memories(memory_type, int(limit))


@tool(
    "保存当前会话上下文，用于跨会话的分析延续。",
    param_desc=[
            "当前正在分析的股票代码",
            "当前分析主题",
            "临时笔记",
        ],
)
def save_session_context(stock_code: str = "", topic: str = "", notes: str = "") -> str:
    """保存当前会话的上下文信息，用于跨会话的分析延续。

    Args:
        stock_code: 当前正在分析的股票代码
        topic: 当前分析主题
        notes: 临时笔记
    """
    content_parts = []
    if stock_code:
        content_parts.append(f"股票: {stock_code}")
    if topic:
        content_parts.append(f"主题: {topic}")
    if notes:
        content_parts.append(f"笔记: {notes}")
    content = " | ".join(content_parts) if content_parts else "空上下文"
    keywords = stock_code if stock_code else ""
    return _memory.save_memory(content, "context", keywords, "会话上下文", 0.3)
