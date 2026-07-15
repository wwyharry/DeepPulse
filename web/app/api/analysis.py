"""分析工具 REST API"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class StrategyCreate(BaseModel):
    filename: str
    content: str


class StrategyUpdate(BaseModel):
    content: str


@router.post("/patterns")
async def recognize_patterns(code: str, days: int = Query(60, ge=1, le=200)):
    """K 线形态识别"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["recognize_kline_patterns"](code=code, days=days)
    return {"code": code, "patterns": result}


@router.post("/screener")
async def screen_stocks(
    min_change: float = Query(None),
    max_change: float = Query(None),
    min_volume_ratio: float = Query(None),
    max_rsi: float = Query(None),
    min_rsi: float = Query(None),
    days: int = Query(60, ge=1, le=200),
):
    """条件选股"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    kwargs = {"days": days}
    if min_change is not None:
        kwargs["min_change"] = min_change
    if max_change is not None:
        kwargs["max_change"] = max_change
    if min_volume_ratio is not None:
        kwargs["min_volume_ratio"] = min_volume_ratio
    if max_rsi is not None:
        kwargs["max_rsi"] = max_rsi
    if min_rsi is not None:
        kwargs["min_rsi"] = min_rsi

    result = TOOL_DISPATCH["screen_stocks"](**kwargs)
    return {"screener": result}


@router.post("/backtest")
async def backtest_stock(code: str, strategy: str = Query("ma_cross"), days: int = Query(250)):
    """策略回测"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["backtest_stock"](code=code, strategy=strategy, days=days)
    return {"code": code, "strategy": strategy, "backtest": result}


@router.post("/charts/generate")
async def generate_chart(code: str, days: int = Query(60, ge=1, le=200)):
    """生成 K 线图表（返回 HTML）"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["generate_chart"](code=code, days=days)
    return {"code": code, "chart": result}


@router.get("/strategies")
async def list_strategies():
    """战法列表"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["list_strategies"]()
    return {"strategies": result}


@router.get("/strategies/{name}")
async def get_strategy(name: str):
    """获取指定战法内容"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["get_strategy"](filename=name)
    return {"strategy": result}


@router.post("/strategies")
async def create_strategy(body: StrategyCreate):
    """创建新战法"""

    from deeppulse.agent.strategy_loader import get_strategy_loader, reload_strategies

    loader = get_strategy_loader()
    filepath = loader.dir / body.filename

    if filepath.exists():
        return {"error": "战法文件已存在", "filename": body.filename}

    # 确保文件名以 .md 结尾
    if not body.filename.endswith(".md"):
        return {"error": "文件名必须以 .md 结尾"}

    filepath.write_text(body.content, encoding="utf-8")

    # 重新加载
    reload_strategies()

    return {"status": "created", "filename": body.filename}


@router.put("/strategies/{name}")
async def update_strategy(name: str, body: StrategyUpdate):
    """更新战法内容"""
    from deeppulse.agent.strategy_loader import get_strategy_loader, reload_strategies

    loader = get_strategy_loader()
    filepath = loader.dir / name

    if not filepath.exists():
        return {"error": "战法文件不存在", "filename": name}

    filepath.write_text(body.content, encoding="utf-8")

    # 重新加载
    reload_strategies()

    return {"status": "updated", "filename": name}


@router.delete("/strategies/{name}")
async def delete_strategy(name: str):
    """删除战法"""
    from deeppulse.agent.strategy_loader import get_strategy_loader, reload_strategies

    loader = get_strategy_loader()
    filepath = loader.dir / name

    if not filepath.exists():
        return {"error": "战法文件不存在", "filename": name}

    filepath.unlink()

    # 重新加载
    reload_strategies()

    return {"status": "deleted", "filename": name}


# ── 新增深度分析 API ──────────────────────────────────


@router.post("/trend")
async def analyze_trend(code: str, days: int = Query(60, ge=1, le=200)):
    """趋势强度分析"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["assess_trend"](code=code, days=days)
    import json

    return json.loads(result)


@router.post("/divergence")
async def detect_divergence(code: str, indicator: str = Query("all"), days: int = Query(60, ge=1, le=200)):
    """背离检测"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["detect_divergence"](code=code, indicator=indicator, days=days)
    import json

    return json.loads(result)


@router.post("/support_resistance")
async def detect_support_resistance(code: str, days: int = Query(60, ge=1, le=200)):
    """支撑压力位检测"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["detect_support_resistance"](code=code, days=days)
    import json

    return json.loads(result)


@router.post("/volume_price")
async def analyze_volume_price(code: str, days: int = Query(60, ge=1, le=200)):
    """量价分析"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["analyze_volume_price"](code=code, days=days)
    import json

    return json.loads(result)


@router.post("/confluence")
async def analyze_confluence(code: str):
    """多周期共振分析"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["analyze_confluence"](code=code)
    import json

    return json.loads(result)


@router.post("/screener_v2")
async def screen_stocks_v2(conditions: str = Query(...), limit: int = Query(20, ge=1, le=100)):
    """高性能选股"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["screen_stocks_v2"](conditions=conditions, limit=limit)
    import json

    return json.loads(result)


@router.post("/fund_flow")
async def get_fund_flow(code: str):
    """个股资金流向"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["stock_fund_flow"](code=code)
    import json

    return json.loads(result)


@router.post("/dragon_tiger")
async def get_dragon_tiger(code: str, days: int = Query(30)):
    """龙虎榜"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["stock_dragon_tiger"](code=code, days=days)
    import json

    return json.loads(result)


@router.post("/stock_news")
async def get_stock_news(code: str, num: int = Query(10)):
    """个股新闻"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["stock_news"](code_or_name=code, num=num)
    import json

    return json.loads(result)


@router.post("/market_news")
async def get_market_news(num: int = Query(10)):
    """市场热点新闻"""
    from deeppulse.agent.tools import TOOL_DISPATCH

    result = TOOL_DISPATCH["market_hot_news"](num=num)
    import json

    return json.loads(result)
