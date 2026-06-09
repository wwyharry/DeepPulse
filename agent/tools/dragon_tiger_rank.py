import json

from agent.tools.decorator import tool

# 龙虎榜

@tool(
    "获取龙虎榜数据（当日上榜个股、净买入额、上榜原因）。用于分析游资和机构动向。",
    param_desc=[
            "日期YYYYMMDD，默认今天",
        ],
)
def dragon_tiger_list(trade_date: str = None) -> str:
    """获取龙虎榜数据（当日上榜个股、净买入额、上榜原因）。

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    from agent.datalink import get_dragon_tiger

    result = get_dragon_tiger(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)

@tool(
    "查询个股龙虎榜历史明细，查看游资和机构席位的买卖动向。",
    param_desc=[
            "股票代码",
            "查询天数",
        ],
)
def stock_dragon_tiger(code: str, days: int = 30) -> str:
    """查询个股龙虎榜历史明细，查看游资和机构席位动向。

    Args:
        code: 股票代码
        days: 查询天数，默认30
    """
    from agent.datalink import get_stock_dragon_tiger_detail

    result = get_stock_dragon_tiger_detail(code, int(days))
    return json.dumps(result, ensure_ascii=False, default=str)