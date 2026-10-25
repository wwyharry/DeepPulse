"""动态推理链 - 根据市场状态自动生成分析策略和推理路径"""

import time

# 市场状态检测结果缓存 (monotonic时间, 状态)，避免频繁请求涨跌停数据
_market_state_cache: tuple[float, str | None] | None = None

# 市场状态与分析策略映射
MARKET_STATE_STRATEGIES = {
    "高潮期": {
        "risk_level": "高",
        "focus": ["高位风险", "获利了结", "仓位控制"],
        "priority_tools": ["market_sentiment", "limit_up_pool", "calc_technical"],
        "analysis_weight": {
            "risk_warning": 0.4,
            "trend_fade": 0.3,
            "sector_rotation": 0.2,
            "stock_selection": 0.1,
        },
        "reasoning": [
            "市场处于高潮期，赚钱效应强但风险累积",
            "重点分析连板高度是否见顶、涨停数是否开始萎缩",
            "高位股注意分歧风险，避免追高",
            "关注板块轮动迹象，寻找补涨方向",
            "建议降低仓位，设置严格止损",
        ],
        "strategy_preference": ["空仓战法", "龙头首阴反包", "分歧转一致"],
        "output_style": "以风险提示为主，操作建议偏向防守",
    },
    "发酵期": {
        "risk_level": "中",
        "focus": ["龙头确认", "板块持续性", "跟风股筛选"],
        "priority_tools": ["sector_ranking", "limit_up_pool", "search_strategy"],
        "analysis_weight": {
            "trend_confirmation": 0.35,
            "leader_identification": 0.3,
            "follower_screening": 0.2,
            "risk_management": 0.15,
        },
        "reasoning": [
            "市场处于发酵期，热点方向逐渐明确",
            "重点确认主线板块的持续性和龙头地位",
            "分析龙头股的辨识度和资金认可度",
            "在主线板块中寻找低位补涨机会",
            "仓位可适当提升，但不超过半仓",
        ],
        "strategy_preference": ["龙头战法", "二板接力", "题材首板"],
        "output_style": "平衡攻防，强调主线确认",
    },
    "启动期": {
        "risk_level": "中低",
        "focus": ["新题材识别", "首板打板", "低位布局"],
        "priority_tools": ["market_hot_news", "market_sentiment", "screen_stocks_v2"],
        "analysis_weight": {
            "new_theme_detection": 0.35,
            "first_board_opportunity": 0.3,
            "low_position_entry": 0.2,
            "volume_confirmation": 0.15,
        },
        "reasoning": [
            "市场处于启动期，情绪开始回暖",
            "重点关注新出现的题材和消息面催化",
            "低位首板股是重点关注对象",
            "分析成交量是否配合放大，确认资金入场",
            "可逐步建仓，以小仓位试探为主",
        ],
        "strategy_preference": ["低位首板", "题材首板", "放量突破"],
        "output_style": "偏进攻，强调机会识别",
    },
    "低迷期": {
        "risk_level": "中高",
        "focus": ["超跌反弹", "强势股筛选", "风险控制"],
        "priority_tools": ["screen_stocks_v2", "calc_technical", "stock_fund_flow"],
        "analysis_weight": {
            "oversold_bounce": 0.35,
            "strong_stock_filter": 0.3,
            "risk_control": 0.25,
            "news_catalyst": 0.1,
        },
        "reasoning": [
            "市场处于低迷期，赚钱效应差",
            "大部分个股下跌，需严格控制仓位",
            "关注严重超跌后的技术性反弹机会",
            "在弱势中筛选逆势走强的个股",
            "等待明确的止跌信号再进场",
        ],
        "strategy_preference": ["超跌低吸", "反核战法", "5日线低吸"],
        "output_style": "极度防守，强调风险规避",
    },
    "冰点期": {
        "risk_level": "极高",
        "focus": ["空仓观望", "极端超跌", "转机信号"],
        "priority_tools": ["market_sentiment", "market_overview", "search_news"],
        "analysis_weight": {
            "empty_position": 0.4,
            "extreme_oversold": 0.25,
            "turning_point_signal": 0.2,
            "news_impact": 0.15,
        },
        "reasoning": [
            "市场处于冰点期，恐慌情绪蔓延",
            "最佳策略是空仓观望，保存实力",
            "极端超跌可能出现技术性反弹，但空间有限",
            "密切关注政策面和消息面的转机信号",
            "如果出现放量长阳等止跌信号，可小仓位参与",
        ],
        "strategy_preference": ["空仓战法", "超跌低吸", "翘板战法"],
        "output_style": "极度保守，以防守和观望为主",
    },
}


def get_analysis_strategy(market_state: str = None) -> dict:
    """根据市场状态获取分析策略

    Args:
        market_state: 高潮期/发酵期/启动期/低迷期/冰点期

    Returns:
        包含分析重点、工具优先级、推理链的策略字典
    """
    if market_state and market_state in MARKET_STATE_STRATEGIES:
        return MARKET_STATE_STRATEGIES[market_state]

    # 默认策略（未确定市场状态时）
    return {
        "risk_level": "中",
        "focus": ["趋势判断", "量价分析", "技术指标"],
        "priority_tools": ["calc_technical", "query_kline", "stock_fund_flow"],
        "analysis_weight": {
            "trend_analysis": 0.3,
            "technical_indicators": 0.3,
            "volume_price": 0.2,
            "news_sentiment": 0.2,
        },
        "reasoning": [
            "先确认市场整体状态和情绪",
            "分析个股技术面和量价配合",
            "搜索相关新闻了解基本面",
            "结合战法库给出操作建议",
        ],
        "strategy_preference": [],
        "output_style": "平衡分析，攻防兼备",
    }


def _profile_value(user_profile: dict, *keys: str) -> str:
    """从用户画像字典中取值，兼容 {"value": ...} 包装与纯字符串两种形态"""
    for key in keys:
        item = user_profile.get(key)
        if isinstance(item, dict):
            return str(item.get("value", "") or "")
        if isinstance(item, str):
            return item
    return ""


def build_dynamic_system_prompt(base_prompt: str, market_state: str = None, user_profile: dict = None) -> str:
    """构建动态 system prompt，融入市场状态和用户画像

    Args:
        base_prompt: 基础 system prompt
        market_state: 当前市场状态（高潮期/发酵期/启动期/低迷期/冰点期）
        user_profile: 用户交易画像。键名兼容中文（"交易风格"/"风险偏好"）
            与数据库英文键（"trading_style"/"risk_tolerance"）；值可为
            {"value": ...} 包装或纯字符串。
    """
    strategy = get_analysis_strategy(market_state)

    sections = []

    # 市场状态分析指引
    if market_state:
        lines = [
            f"## 当前市场状态: {market_state}",
            f"- 风险等级: {strategy['risk_level']}",
            f"- 分析重点: {'、'.join(strategy['focus'])}",
            f"- 工具优先级（并行批量调用）: {'、'.join(strategy['priority_tools'])}",
            f"- 操作风格: {strategy['output_style']}",
            "",
            "### 动态推理步骤（以下步骤指导思考顺序；工具调用按批并行，不必逐步单发）",
        ]
        for i, step in enumerate(strategy["reasoning"], 1):
            lines.append(f"{i}. {step}")

        if strategy["strategy_preference"]:
            lines.append(f"\n### 推荐战法: {'、'.join(strategy['strategy_preference'])}")
        sections.append("\n".join(lines))

    # 用户画像适配
    if user_profile:
        style = _profile_value(user_profile, "交易风格", "trading_style")
        risk = _profile_value(user_profile, "风险偏好", "risk_tolerance")

        if style or risk:
            lines = [
                "## 用户画像适配（规则提取的推测，可能有误；与用户当前表述冲突时以当前表述为准）",
                f"- 交易风格: {style or '未确定'}",
                f"- 风险偏好: {risk or '未确定'}",
            ]
            if "低吸" in style:
                lines.append("- 优先推荐低吸类战法，避免追高建议")
            elif "打板" in style:
                lines.append("- 可推荐打板和接力战法，强调封板质量")
            elif "追高" in style:
                lines.append("- 提醒追高风险，建议设置严格止损")

            # 词表与画像提取对齐：保守型/稳健型 同义（提取层输出「保守型」）
            if "稳健" in risk or "保守" in risk:
                lines.append("- 操作建议偏向稳健，控制仓位，严格止损")
            elif "激进" in risk:
                lines.append("- 可推荐进攻性操作，但仍需提示风险")
            sections.append("\n".join(lines))

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


def detect_market_state(ttl: float = 300.0, timeout: float = 5.0) -> str | None:
    """从市场情绪数据推断当前市场状态

    通过 ``get_market_sentiment()`` 的情绪评级解析出
    高潮期/发酵期/启动期/低迷期/冰点期 之一。

    市场情绪需要请求涨跌停数据（网络），因此结果按 ``ttl`` 秒缓存，
    且单次取数不超过 ``timeout`` 秒——弱网/断网时立即降级返回 None，
    不阻塞 Agent 启动。

    Args:
        ttl: 结果缓存秒数（默认 5 分钟）
        timeout: 单次取数超时秒数

    Returns:
        市场状态字符串；数据不可用、非交易日（无任何涨跌停数据）
        或解析失败时返回 None（调用方应回退到默认分析策略）。
    """
    global _market_state_cache
    now = time.monotonic()
    if _market_state_cache is not None:
        cached_at, cached_state = _market_state_cache
        if now - cached_at < ttl:
            return cached_state

    state = _fetch_market_state(timeout)
    _market_state_cache = (now, state)
    return state


def _fetch_market_state(timeout: float) -> str | None:
    """取市场情绪并解析状态（带超时，失败返回 None）"""
    try:
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FuturesTimeout

        from deeppulse.agent.market import get_market_sentiment

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(get_market_sentiment)
            data = future.result(timeout=timeout)
        except FuturesTimeout:
            future.cancel()
            return None
        finally:
            executor.shutdown(wait=False)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    # 非交易日/数据未更新时涨停数与跌停数均为 0，不能误判为冰点期
    if not data.get("涨停数") and not data.get("跌停数"):
        return None

    rating = str(data.get("情绪评级", ""))
    for state in ("高潮期", "发酵期", "启动期", "低迷期", "冰点期"):
        if state in rating:
            return state
    return None
