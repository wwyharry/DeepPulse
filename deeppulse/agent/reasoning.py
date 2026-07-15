"""动态推理链 - 根据市场状态自动生成分析策略和推理路径"""

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
        "strategy_preference": ["龙头战法", "二板接力", "板块首板"],
        "output_style": "平衡攻防，强调主线确认",
    },
    "启动期": {
        "risk_level": "中低",
        "focus": ["新题材识别", "首板打板", "低位布局"],
        "priority_tools": ["market_hot_news", "market_sentiment", "screen_stocks"],
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
        "priority_tools": ["screen_stocks", "calc_technical", "stock_fund_flow"],
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


def build_dynamic_system_prompt(base_prompt: str, market_state: str = None, user_profile: dict = None) -> str:
    """构建动态 system prompt，融入市场状态和用户画像

    Args:
        base_prompt: 基础 system prompt
        market_state: 当前市场状态
        user_profile: 用户交易画像
    """
    strategy = get_analysis_strategy(market_state)

    additions = []

    # 市场状态分析指引
    if market_state:
        additions.append(f"""
## 当前市场状态: {market_state}
- 风险等级: {strategy["risk_level"]}
- 分析重点: {"、".join(strategy["focus"])}
- 工具优先级: {" → ".join(strategy["priority_tools"])}
- 操作风格: {strategy["output_style"]}

### 动态推理步骤
""")
        for i, step in enumerate(strategy["reasoning"], 1):
            additions.append(f"{i}. {step}")

        if strategy["strategy_preference"]:
            additions.append(f"\n### 推荐战法: {'、'.join(strategy['strategy_preference'])}")

    # 用户画像适配
    if user_profile:
        style = user_profile.get("交易风格", {}).get("value", "")
        risk = user_profile.get("风险偏好", {}).get("value", "")

        if style or risk:
            additions.append(f"""
## 用户画像适配
- 交易风格: {style or "未确定"}
- 风险偏好: {risk or "未确定"}
""")
            if "低吸" in style:
                additions.append("- 优先推荐低吸类战法，避免追高建议")
            elif "打板" in style:
                additions.append("- 可推荐打板和接力战法，强调封板质量")
            elif "追高" in style:
                additions.append("- 提醒追高风险，建议设置严格止损")

            if "稳健" in risk:
                additions.append("- 操作建议偏向稳健，控制仓位，严格止损")
            elif "激进" in risk:
                additions.append("- 可推荐进攻性操作，但仍需提示风险")

    if additions:
        return base_prompt + "\n" + "".join(additions)
    return base_prompt


def analyze_stock_dynamic(code: str, market_state: str = None, user_profile: dict = None) -> dict:
    """生成单只股票的动态分析计划

    Returns:
        包含分析步骤、重点关注、推荐工具的分析计划
    """
    strategy = get_analysis_strategy(market_state)

    plan = {
        "code": code,
        "market_state": market_state or "待判断",
        "risk_level": strategy["risk_level"],
        "steps": [],
    }

    # 基础步骤
    plan["steps"].append(
        {
            "action": "update_stock",
            "reason": "确保数据最新",
            "priority": "必做",
        }
    )

    plan["steps"].append(
        {
            "action": "realtime_price",
            "reason": "获取实时价格",
            "priority": "必做",
        }
    )

    # 根据市场状态动态调整
    for tool in strategy["priority_tools"]:
        plan["steps"].append(
            {
                "action": tool,
                "reason": f"市场{market_state}下的重点分析",
                "priority": "高",
            }
        )

    # 标准分析步骤
    standard_steps = [
        ("calc_technical", "技术指标全貌"),
        ("recognize_kline_patterns", "K线形态识别"),
        ("stock_fund_flow", "资金流向"),
        ("stock_news", "消息面"),
        ("search_strategy", "匹配战法"),
        ("search_memory", "历史记忆"),
    ]

    existing_tools = {s["action"] for s in plan["steps"]}
    for tool, reason in standard_steps:
        if tool not in existing_tools:
            plan["steps"].append(
                {
                    "action": tool,
                    "reason": reason,
                    "priority": "中",
                }
            )

    plan["steps"].append(
        {
            "action": "save_prediction",
            "reason": "保存预测结论",
            "priority": "必做",
        }
    )

    plan["analysis_focus"] = strategy["focus"]
    plan["strategy_preference"] = strategy["strategy_preference"]
    plan["output_style"] = strategy["output_style"]

    return plan


def generate_market_state_check_plan() -> dict:
    """生成市场状态判断的工具调用计划"""
    return {
        "steps": [
            {"action": "market_sentiment", "reason": "获取情绪评级和涨跌停数据"},
            {"action": "limit_up_pool", "reason": "分析涨停梯队和连板高度"},
            {"action": "sector_ranking", "reason": "发现热点板块方向"},
            {"action": "market_overview", "reason": "涨跌统计和市场概况"},
            {"action": "market_hot_news", "reason": "消息面催化"},
        ],
        "判断标准": {
            "高潮期": "涨停数>100，连板高度>5板，情绪评级=高潮",
            "发酵期": "涨停数50-100，连板高度3-5板，主线明确",
            "启动期": "涨停数30-50，新题材出现，情绪从低位回升",
            "低迷期": "涨停数<30，跌停数增多，赚钱效应差",
            "冰点期": "涨停数<15，跌停>涨停，恐慌情绪蔓延",
        },
    }
