"""行情数据 REST API"""

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/stocks/search")
async def search_stocks(q: str = Query(..., description="搜索关键词")):
    """搜索股票"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["search_stock"](keyword=q)
    return {"results": result}


@router.get("/stocks/{code}")
async def get_stock_info(code: str):
    """获取股票基本信息"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["query_stock_info"](code=code)
    return {"info": result}


@router.get("/stocks/{code}/kline")
async def get_kline(code: str, days: int = Query(60, ge=1, le=500)):
    """获取 K 线数据"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["query_kline"](code=code, days=days)
    return {"code": code, "days": days, "kline": result}


@router.get("/stocks/{code}/realtime")
async def get_realtime(code: str):
    """获取实时行情"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["realtime_price"](code=code)
    return {"realtime": result}


@router.get("/stocks/{code}/technical")
async def get_technical(code: str, days: int = Query(60, ge=1, le=500)):
    """获取技术指标"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["calc_technical"](code=code, days=days)
    return {"code": code, "technical": result}


@router.get("/overview")
async def get_market_overview():
    """大盘概览"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["market_overview"]()
    return {"overview": result}


@router.get("/sentiment")
async def get_market_sentiment():
    """市场情绪"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["market_sentiment"]()
    return {"sentiment": result}


@router.get("/sectors")
async def get_sector_ranking(board_type: str = Query("industry"), top_n: int = Query(30, ge=1, le=100)):
    """板块排名"""

    from deeppulse.agent.market import get_sector_ranking

    result = get_sector_ranking(board_type=board_type, top_n=top_n)
    return result


@router.get("/sectors/hot")
async def get_hot_industries():
    """从涨停股推断热门行业"""
    from deeppulse.agent.market import get_hot_industries_from_zt

    result = get_hot_industries_from_zt()
    return result


@router.get("/limit-up")
async def get_limit_up_pool():
    """涨停池"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["limit_up_pool"]()
    return {"limit_up": result}
