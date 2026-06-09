import json

from agent.tools.decorator import tool

@tool("获取北向资金（沪股通+深股通）近5日净流入数据和趋势判断。北向资金是重要的市场风向标。")
def northbound_flow() -> str:
    """获取北向资金（沪股通+深股通）近5日净流入数据和趋势判断。"""
    from agent.datalink import get_northbound_flow

    result = get_northbound_flow()
    return json.dumps(result, ensure_ascii=False, default=str)


@tool("获取板块资金流向（行业+概念板块主力净流入排行Top10）。用于发现资金主攻方向。")
def sector_fund_flow() -> str:
    """获取板块资金流向（行业板块+概念板块主力净流入排行Top10）。"""
    from agent.datalink import get_sector_fund_flow

    result = get_sector_fund_flow()
    return json.dumps(result, ensure_ascii=False, default=str)