"""市场情绪与板块工具"""

import json

from deeppulse.agent.market import get_limit_up_pool as _get_limit_up_pool
from deeppulse.agent.market import get_market_sentiment as _get_market_sentiment
from deeppulse.agent.market import get_sector_ranking as _get_sector_ranking


def market_sentiment(trade_date: str = None) -> str:
    """获取市场情绪综合分析"""
    result = _get_market_sentiment(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


def limit_up_pool(trade_date: str = None) -> str:
    """获取涨停股池"""
    result = _get_limit_up_pool(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


def sector_ranking(board_type: str = "industry", top_n: int = 10) -> str:
    """获取板块涨跌排行"""
    result = _get_sector_ranking(board_type, int(top_n))
    return json.dumps(result, ensure_ascii=False, default=str)


def stock_fund_flow(code: str) -> str:
    """获取个股资金流向（多数据源，自动降级）"""
    from deeppulse.agent.fund_flow import get_stock_fund_flow

    result = get_stock_fund_flow(code)
    return json.dumps(result, ensure_ascii=False, default=str)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "market_sentiment",
            "description": "获取市场情绪综合分析，包括涨停数、跌停数、炸板数、连板高度、情绪评级和操作建议。用于判断市场整体情绪状态（高潮/发酵/启动/低迷/冰点）。",
            "parameters": {
                "type": "object",
                "properties": {"trade_date": {"type": "string", "description": "交易日期 YYYYMMDD，默认今天"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "limit_up_pool",
            "description": "获取涨停股池，包含涨停股票的代码、名称、首次封板时间、连板数、所属行业等。用于分析市场热点方向和连板梯队。",
            "parameters": {
                "type": "object",
                "properties": {"trade_date": {"type": "string", "description": "交易日期 YYYYMMDD，默认今天"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sector_ranking",
            "description": "获取行业板块或概念板块的涨跌排行，包含涨跌幅和主力净流入。用于发现当日市场热点板块和资金流向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "board_type": {
                        "type": "string",
                        "description": "板块类型: industry(行业)/concept(概念)",
                        "default": "industry",
                    },
                    "top_n": {"type": "integer", "description": "返回前N个板块，默认10", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_fund_flow",
            "description": "获取个股资金流向，包括主力、超大单、大单、中单、小单的净流入情况。",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "6位股票代码"}},
                "required": ["code"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "market_sentiment": market_sentiment,
    "limit_up_pool": limit_up_pool,
    "sector_ranking": sector_ranking,
    "stock_fund_flow": stock_fund_flow,
}
