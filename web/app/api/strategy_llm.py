"""LLM 量化策略生成 REST API

基于 LLM 大模型 + 因子库的策略生成、回测与自动调优：
- GET  /factors         因子库目录（含参数网格范围）
- POST /generate        LLM 生成策略 + 快速回测
- POST /optimize        LLM 分析 + 网格搜索的自动优化循环
- POST /save            保存 AI 生成的策略到个股策略库
- GET  /saved           列出已保存的个股策略
- DELETE /saved/{id}    删除已保存的个股策略
- POST /saved/{id}/rebacktest  用已保存策略重新回测
"""

import re
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from deeppulse.strategy.backtest import backtest_stock
from deeppulse.strategy.factor_library import list_factors
from deeppulse.strategy.llm_generator import LLMGenerationError, LLMStrategyGenerator, build_kline_stats
from deeppulse.strategy.optimizer import AutoOptimizer
from deeppulse.strategy.spec import StrategySpec, compile_signal_func

router = APIRouter()

_CODE_PATTERN = re.compile(r"^\d{6}$")


def _validate_code(code: str) -> None:
    if not _CODE_PATTERN.match(code):
        raise HTTPException(status_code=400, detail=f"股票代码格式错误: {code}（应为 6 位数字）")


def _load_kline_with_indicators(code: str, days: int):
    """取K线并计算技术指标"""
    from deeppulse.agent.indicators import TechnicalIndicators
    from deeppulse.src.query import StockQuery

    query = StockQuery()
    end = date.today()
    start = end - timedelta(days=int(days * 1.6))
    df = query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"股票 {code} 无K线数据，请先运行数据采集脚本")
    df = df.sort_values("trade_date").reset_index(drop=True)
    return TechnicalIndicators.add_all(df)


# ============ 请求模型 ============


class GenerateRequest(BaseModel):
    """LLM 策略生成请求"""

    code: str
    days: int = Field(default=750, ge=120, le=2000)
    hint: str | None = None


class OptimizeRequest(BaseModel):
    """自动优化请求"""

    code: str
    days: int = Field(default=750, ge=120, le=2000)
    max_iterations: int = Field(default=5, ge=1, le=10)
    hint: str | None = None


class SaveStrategyRequest(BaseModel):
    """保存AI生成的策略"""

    stock_code: str
    stock_name: str = ""
    name: str = ""
    spec: dict  # StrategySpec.to_dict()
    backtest: dict = {}  # 回测结果
    llm_rationale: str = ""


# ============ 路由 ============


@router.get("/factors")
def get_factors():
    """获取因子库目录（含每个参数的网格搜索范围）"""
    factors = [
        {
            "id": f.id,
            "name": f.name,
            "description": f.description,
            "params": {
                k: {"default": v.default, "min": v.min, "max": v.max, "step": v.step}
                for k, v in f.params.items()
            },
        }
        for f in list_factors()
    ]
    return {"factors": factors}


@router.post("/generate")
def generate_strategy(req: GenerateRequest):
    """LLM 生成多因子策略并快速回测"""
    _validate_code(req.code)
    df = _load_kline_with_indicators(req.code, req.days)
    kline_stats = build_kline_stats(df)

    generator = LLMStrategyGenerator()
    try:
        spec = generator.generate(req.code, kline_stats, hint=req.hint)
    except LLMGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {e}") from e

    try:
        signal_func = compile_signal_func(spec)
        result = backtest_stock(
            req.code,
            signal_func=signal_func,
            days=req.days,
            exit_strategy_preset=spec.exit_preset,
            exit_strategy_params=spec.exit_params or None,
        )
        result.strategy_name = spec.name
        backtest = result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测失败: {e}") from e

    return {"spec": spec.to_dict(), "kline_stats": kline_stats, "backtest": backtest}


@router.post("/optimize")
def optimize_strategy(req: OptimizeRequest):
    """LLM + 网格搜索自动优化循环，返回最优混合多因子策略"""
    _validate_code(req.code)
    df = _load_kline_with_indicators(req.code, req.days)
    kline_stats = build_kline_stats(df)

    optimizer = AutoOptimizer()
    report = optimizer.optimize(
        req.code,
        days=req.days,
        max_iterations=req.max_iterations,
        hint=req.hint,
        kline_stats=kline_stats,
    )
    return report.to_dict()


# ============ 个股策略管理 API ============


@router.post("/save")
def save_strategy(req: SaveStrategyRequest):
    """保存 AI 生成的策略到个股策略库"""
    _validate_code(req.stock_code)

    # 校验 spec 合法性
    try:
        spec = StrategySpec.from_dict(req.spec)
        errors = spec.validate()
        if errors:
            raise HTTPException(status_code=400, detail=f"策略规格非法: {'; '.join(errors)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析策略规格失败: {e}") from e

    from deeppulse.strategy.models import StockStrategy
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()

    # 获取股票名称
    stock_name = req.stock_name
    if not stock_name:
        stock_name = _get_stock_name(req.stock_code)

    ss = StockStrategy(
        stock_code=req.stock_code,
        stock_name=stock_name,
        name=req.name or spec.name,
        llm_rationale=req.llm_rationale or spec.llm_rationale,
        backtest_result=req.backtest,
    )
    ss.set_spec(spec)

    success = repo.save_stock_strategy(ss)
    if not success:
        raise HTTPException(status_code=500, detail="保存策略失败")

    return {
        "status": "saved",
        "strategy": {
            "id": ss.id,
            "stock_code": ss.stock_code,
            "stock_name": ss.stock_name,
            "name": ss.name,
        },
    }


def _get_stock_name(code: str) -> str:
    """从数据库获取股票名称"""
    try:
        import duckdb
        from deeppulse import config

        if config.DB_PATH.exists():
            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                row = conn.execute(
                    "SELECT name FROM stock_info WHERE code = ?", [code]
                ).fetchone()
                if row and row[0]:
                    return row[0]
            finally:
                conn.close()
    except Exception:
        pass
    return code


@router.get("/saved")
def list_saved_strategies(stock_code: str = None):
    """列出已保存的个股策略，可按 stock_code 筛选"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()

    if stock_code:
        strategies = repo.list_stock_strategies(stock_code=stock_code)
    else:
        strategies = repo.list_stock_strategies()

    return {
        "total": len(strategies),
        "strategies": [s.to_dict() for s in strategies],
    }


@router.delete("/saved/{strategy_id}")
def delete_saved_strategy(strategy_id: str):
    """删除已保存的个股策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    success = repo.delete_stock_strategy(strategy_id)
    if not success:
        raise HTTPException(status_code=404, detail="策略不存在")

    return {"status": "deleted"}


@router.post("/saved/{strategy_id}/rebacktest")
def rebacktest_saved_strategy(
    strategy_id: str,
    days: int = Query(default=500, ge=120, le=2000),
    exit_strategy_preset: str = Query(default="balanced"),
):
    """用已保存的策略重新回测"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    ss = repo.get_stock_strategy(strategy_id)
    if not ss:
        raise HTTPException(status_code=404, detail="策略不存在")

    spec = ss.get_spec()
    try:
        signal_func = compile_signal_func(spec)
        result = backtest_stock(
            ss.stock_code,
            signal_func=signal_func,
            days=days,
            exit_strategy_preset=exit_strategy_preset,
            exit_strategy_params=spec.exit_params or None,
        )
        result.strategy_name = ss.name
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测失败: {e}") from e

    return {
        "stock_code": ss.stock_code,
        "stock_name": ss.stock_name,
        "strategy_name": ss.name,
        "backtest": result.to_dict(),
        "summary": result.summary(),
    }


@router.get("/stock-codes")
def get_stock_codes_with_strategies():
    """获取所有有策略的股票代码及策略数量"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    codes = repo.get_stock_codes_with_strategies()
    return {"stocks": codes}
