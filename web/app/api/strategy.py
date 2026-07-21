"""策略平台 REST API"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


# ============ 请求模型 ============


class StrategyCreate(BaseModel):
    """创建策略请求"""

    name: str
    content: str
    category: str = ""
    tags: list[str] = []
    description: str = ""
    risk_level: str = "medium"


class StrategyUpdate(BaseModel):
    """更新策略请求"""

    name: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    description: Optional[str] = None
    risk_level: Optional[str] = None
    status: Optional[str] = None


class BacktestRequest(BaseModel):
    """回测请求"""

    strategy_id: str = ""  # 旧版：通用策略ID
    stock_code: str = ""
    days: int = 500
    initial_capital: float = 100000
    stop_loss: float = 0.05
    take_profit: float = 0.10
    max_hold_days: int = 20
    use_dynamic_exit: bool = True  # 是否使用动态退出策略
    exit_strategy_preset: str = "balanced"  # 动态退出策略预设 (conservative/balanced/aggressive)
    exit_strategy_params: Optional[dict] = None  # 自定义退出策略参数
    stock_strategy_id: str = ""  # 新版：个股AI策略ID（优先使用）


class OptimizeRequest(BaseModel):
    """参数优化请求"""

    strategy_id: str
    stock_code: str
    param_grid: dict
    days: int = 250
    use_dynamic_exit: bool = True
    exit_strategy_preset: str = "balanced"


class PortfolioCreate(BaseModel):
    """创建组合请求"""

    name: str
    description: str = ""
    strategies: list = []
    total_capital: float = 1000000
    risk_budget: float = 0.02


class PortfolioUpdate(BaseModel):
    """更新组合请求"""

    name: Optional[str] = None
    description: Optional[str] = None
    total_capital: Optional[float] = None
    risk_budget: Optional[str] = None


# ============ 策略管理 API ============


@router.get("/list")
async def list_strategies():
    """获取策略列表"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    strategies = repo.load_all()

    return {
        "total": len(strategies),
        "strategies": [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "tags": s.tags,
                "risk_level": s.risk_level,
                "status": s.status,
                "version": s.version,
                "filename": s.filename,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in strategies
        ],
    }


@router.post("/create")
async def create_strategy(body: StrategyCreate):
    """创建新策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()

    # 创建策略
    strategy = repo.create_strategy(
        name=body.name,
        content=body.content,
        category=body.category,
        tags=body.tags,
        description=body.description,
        risk_level=body.risk_level,
    )

    return {
        "status": "created",
        "strategy": {
            "id": strategy.id,
            "name": strategy.name,
            "filename": strategy.filename,
        },
    }


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str):
    """获取策略详情"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    strategy = repo.get_strategy(strategy_id)

    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    return {
        "strategy": strategy.to_dict(),
    }


@router.put("/{strategy_id}")
async def update_strategy(strategy_id: str, body: StrategyUpdate):
    """更新策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()

    # 准备更新数据
    update_data = {}
    if body.name is not None:
        update_data["name"] = body.name
    if body.content is not None:
        update_data["content"] = body.content
    if body.category is not None:
        update_data["category"] = body.category
    if body.tags is not None:
        update_data["tags"] = body.tags
    if body.description is not None:
        update_data["description"] = body.description
    if body.risk_level is not None:
        update_data["risk_level"] = body.risk_level
    if body.status is not None:
        update_data["status"] = body.status

    # 更新策略
    strategy = repo.update_strategy(strategy_id, **update_data)

    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    return {
        "status": "updated",
        "strategy": {
            "id": strategy.id,
            "name": strategy.name,
            "version": strategy.version,
        },
    }


@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: str):
    """删除策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    success = repo.delete_strategy(strategy_id)

    if not success:
        raise HTTPException(status_code=404, detail="策略不存在")

    return {"status": "deleted"}


@router.get("/{strategy_id}/versions")
async def get_strategy_versions(strategy_id: str):
    """获取策略版本历史"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    versions = repo.get_versions(strategy_id)

    return {
        "strategy_id": strategy_id,
        "versions": [v.to_dict() for v in versions],
    }


@router.post("/search")
async def search_strategies(query: str = Query(...), top_k: int = Query(5, ge=1, le=20)):
    """搜索策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    results = repo.search(query, top_k)

    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


@router.post("/import")
async def import_strategy(filename: str, content: str):
    """导入策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()

    # 检查文件名
    if not filename.endswith(".md"):
        filename += ".md"

    # 创建策略
    from deeppulse.strategy.models import Strategy

    strategy = Strategy.from_markdown(filename, content)
    repo.save_strategy(strategy)

    return {
        "status": "imported",
        "strategy": {
            "id": strategy.id,
            "name": strategy.name,
            "filename": strategy.filename,
        },
    }


@router.get("/{strategy_id}/export")
async def export_strategy(strategy_id: str):
    """导出策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    strategy = repo.get_strategy(strategy_id)

    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    return {
        "filename": strategy.filename,
        "content": strategy.content,
    }


@router.post("/{strategy_id}/clone")
async def clone_strategy(strategy_id: str, new_name: str = Query(...)):
    """克隆策略"""
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    strategy = repo.get_strategy(strategy_id)

    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    # 创建克隆
    new_strategy = repo.create_strategy(
        name=new_name,
        content=strategy.content,
        category=strategy.category,
        tags=strategy.tags,
        description=strategy.description,
        risk_level=strategy.risk_level,
        conditions=strategy.conditions,
        parameters=strategy.parameters,
    )

    return {
        "status": "cloned",
        "strategy": {
            "id": new_strategy.id,
            "name": new_strategy.name,
            "filename": new_strategy.filename,
        },
    }


# ============ 策略回测 API ============


@router.post("/backtest")
async def run_backtest(body: BacktestRequest):
    """运行回测

    支持两种模式：
    1. stock_strategy_id：使用已保存的个股AI策略（StrategySpec）
    2. strategy_id + stock_code：使用旧版通用策略
    """
    from deeppulse.strategy.backtest import backtest_stock, SIGNAL_FUNCTIONS
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()

    # 优先使用新版个股策略
    if body.stock_strategy_id:
        ss = repo.get_stock_strategy(body.stock_strategy_id)
        if not ss:
            raise HTTPException(status_code=404, detail="个股策略不存在")

        from deeppulse.strategy.spec import compile_signal_func

        spec = ss.get_spec()
        try:
            signal_func = compile_signal_func(spec)
            result = backtest_stock(
                code=ss.stock_code,
                signal_func=signal_func,
                days=body.days,
                initial_capital=body.initial_capital,
                use_dynamic_exit=body.use_dynamic_exit,
                exit_strategy_preset=body.exit_strategy_preset,
                exit_strategy_params=body.exit_strategy_params,
            )
            result.strategy_name = ss.name
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"回测失败: {e}") from e

        return {
            "backtest": result.to_dict(),
            "summary": result.summary(),
        }

    # 旧版通用策略回测
    if not body.strategy_id or not body.stock_code:
        raise HTTPException(
            status_code=400,
            detail="需要提供 stock_strategy_id 或 strategy_id + stock_code",
        )

    strategy = repo.get_strategy(body.strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    # 确定信号函数名称
    strategy_name = strategy.name
    if strategy_name not in SIGNAL_FUNCTIONS:
        content = strategy.content.lower()
        if "macd" in content:
            strategy_name = "macd_cross"
        elif "ma" in content or "均线" in content:
            strategy_name = "ma_cross"
        elif "rsi" in content:
            strategy_name = "rsi_oversold"
        elif "布林" in content or "boll" in content:
            strategy_name = "boll_bounce"
        elif "kdj" in content:
            strategy_name = "kdj_cross"
        else:
            strategy_name = "macd_cross"

    result = backtest_stock(
        code=body.stock_code,
        strategy=strategy_name,
        days=body.days,
        initial_capital=body.initial_capital,
        stop_loss=body.stop_loss,
        take_profit=body.take_profit,
        max_hold=body.max_hold_days,
        use_dynamic_exit=body.use_dynamic_exit,
        exit_strategy_preset=body.exit_strategy_preset,
        exit_strategy_params=body.exit_strategy_params,
    )

    from deeppulse.strategy.models import BacktestResult

    backtest_result = BacktestResult(
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        stock_code=body.stock_code,
        start_date=result.start_date,
        end_date=result.end_date,
        initial_capital=result.initial_capital,
        final_capital=result.final_capital,
        total_return=result.total_return,
        annual_return=result.annual_return,
        max_drawdown=result.max_drawdown,
        win_rate=result.win_rate,
        sharpe_ratio=result.sharpe_ratio,
        total_trades=result.total_trades,
        parameters={
            "days": body.days,
            "stop_loss": body.stop_loss,
            "take_profit": body.take_profit,
            "max_hold_days": body.max_hold_days,
            "use_dynamic_exit": body.use_dynamic_exit,
            "exit_strategy_preset": body.exit_strategy_preset,
        },
        trades=result.trades,
        exit_strategy_type=result.exit_strategy_type,
        exit_statistics=result.exit_statistics,
        exit_analysis=result.exit_analysis,
    )
    repo.save_backtest_result(backtest_result)

    return {
        "backtest": result.to_dict(),
        "summary": result.summary(),
    }


@router.get("/exit-strategies/presets")
async def get_exit_strategy_presets():
    """获取预设退出策略列表"""
    from deeppulse.strategy.exit_strategies import list_preset_strategies

    presets = list_preset_strategies()

    return {
        "presets": presets,
    }


@router.get("/exit-strategies/presets/{preset_name}")
async def get_exit_strategy_preset(preset_name: str):
    """获取指定预设退出策略详情"""
    from deeppulse.strategy.exit_strategies import get_preset_strategy

    try:
        strategy = get_preset_strategy(preset_name)
        return {
            "name": preset_name,
            "strategy": strategy.to_dict(),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/backtest/batch")
async def batch_backtest(
    strategy_id: str = Query(...),
    stock_codes: str = Query(...),
    days: int = Query(250),
    use_dynamic_exit: bool = Query(True),
    exit_strategy_preset: str = Query("balanced"),
):
    """批量回测"""
    from deeppulse.strategy.backtest import backtest_strategy_multi_stock
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    strategy = repo.get_strategy(strategy_id)

    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    # 解析股票代码
    codes = [c.strip() for c in stock_codes.split(",") if c.strip()]

    # 运行批量回测
    result = backtest_strategy_multi_stock(
        strategy=strategy.name,
        codes=codes,
        days=days,
        use_dynamic_exit=use_dynamic_exit,
        exit_strategy_preset=exit_strategy_preset,
    )

    return result


@router.post("/backtest/optimize")
async def optimize_backtest(body: OptimizeRequest):
    """参数优化"""
    from deeppulse.strategy.backtest import optimize_strategy
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    strategy = repo.get_strategy(body.strategy_id)

    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    result = optimize_strategy(
        code=body.stock_code,
        strategy_name=strategy.name,
        param_grid=body.param_grid,
        days=body.days,
        use_dynamic_exit=body.use_dynamic_exit,
        exit_strategy_preset=body.exit_strategy_preset,
    )

    return result


@router.post("/backtest/monte-carlo")
async def monte_carlo_simulation(
    strategy_id: str = Query(...),
    stock_code: str = Query(...),
    days: int = Query(500),
    n_simulations: int = Query(1000),
):
    """蒙特卡洛模拟"""
    from deeppulse.strategy.backtest import monte_carlo_simulation
    from deeppulse.strategy.engine import get_engine
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()
    strategy = repo.get_strategy(strategy_id)

    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    # 先运行回测获取交易记录
    engine = get_engine()
    result = engine.backtest_strategy(
        strategy=strategy,
        stock_code=stock_code,
        days=days,
    )

    # 运行蒙特卡洛模拟
    mc_result = monte_carlo_simulation(
        total_return=result.total_return,
        trades=result.trades,
        n_simulations=n_simulations,
    )

    return mc_result


# ============ 策略健康度 API ============


@router.get("/health/dashboard")
async def health_dashboard():
    """获取所有策略的健康度面板数据"""
    from deeppulse.strategy.health import get_health_monitor

    monitor = get_health_monitor()
    return monitor.get_dashboard()


@router.get("/health/trend/{strategy_id}")
async def health_trend(strategy_id: str, days: int = Query(30)):
    """获取某策略的得分趋势"""
    from deeppulse.strategy.health import get_health_monitor

    monitor = get_health_monitor()
    return monitor.get_trend(strategy_id, days=days)


@router.post("/health/scan")
async def health_scan():
    """手动触发一次健康度扫描"""
    from deeppulse.strategy.health import get_health_monitor

    monitor = get_health_monitor()
    snapshots = monitor.scan_all()

    return {
        "total": len(snapshots),
        "snapshots": [s.to_dict() for s in snapshots],
    }


# ============ AI 策略分析 API ============


@router.post("/ai-analyze/{stock_strategy_id}")
async def ai_analyze_strategy(stock_strategy_id: str):
    """用已保存的策略 + LLM 分析当前行情，预测买卖点"""
    from deeppulse.strategy.performance import get_ai_analyzer

    analyzer = get_ai_analyzer()
    result = analyzer.analyze_with_strategy(stock_strategy_id)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return result


# ============ 策略组合 API ============


@router.get("/portfolios/list")
async def list_portfolios():
    """获取组合列表"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    portfolios = manager.list_portfolios()

    return {
        "total": len(portfolios),
        "portfolios": portfolios,
    }


@router.post("/portfolios/create")
async def create_portfolio(body: PortfolioCreate):
    """创建组合"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    portfolio = manager.create_portfolio(
        name=body.name,
        description=body.description,
        strategies=body.strategies,
        total_capital=body.total_capital,
        risk_budget=body.risk_budget,
    )

    return {
        "status": "created",
        "portfolio": portfolio.to_dict(),
    }


@router.get("/portfolios/{portfolio_id}")
async def get_portfolio(portfolio_id: str):
    """获取组合详情"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    portfolio = manager.get_portfolio(portfolio_id)

    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")

    return {
        "portfolio": portfolio.to_dict(),
    }


@router.put("/portfolios/{portfolio_id}")
async def update_portfolio(portfolio_id: str, body: PortfolioUpdate):
    """更新组合"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()

    update_data = {}
    if body.name is not None:
        update_data["name"] = body.name
    if body.description is not None:
        update_data["description"] = body.description
    if body.total_capital is not None:
        update_data["total_capital"] = body.total_capital
    if body.risk_budget is not None:
        update_data["risk_budget"] = body.risk_budget

    portfolio = manager.update_portfolio(portfolio_id, **update_data)

    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")

    return {
        "status": "updated",
        "portfolio": portfolio.to_dict(),
    }


@router.delete("/portfolios/{portfolio_id}")
async def delete_portfolio(portfolio_id: str):
    """删除组合"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    success = manager.delete_portfolio(portfolio_id)

    if not success:
        raise HTTPException(status_code=404, detail="组合不存在")

    return {"status": "deleted"}


@router.post("/portfolios/{portfolio_id}/strategies")
async def add_strategy_to_portfolio(
    portfolio_id: str,
    strategy_id: str = Query(...),
    weight: float = Query(0.1),
):
    """添加策略到组合"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    success = manager.add_strategy_to_portfolio(
        portfolio_id=portfolio_id,
        strategy_id=strategy_id,
        weight=weight,
    )

    if not success:
        raise HTTPException(status_code=400, detail="添加失败")

    return {"status": "added"}


@router.delete("/portfolios/{portfolio_id}/strategies/{strategy_id}")
async def remove_strategy_from_portfolio(portfolio_id: str, strategy_id: str):
    """从组合移除策略"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    success = manager.remove_strategy_from_portfolio(portfolio_id, strategy_id)

    if not success:
        raise HTTPException(status_code=400, detail="移除失败")

    return {"status": "removed"}


@router.get("/portfolios/{portfolio_id}/performance")
async def get_portfolio_performance(portfolio_id: str):
    """获取组合绩效"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    result = manager.get_portfolio_performance(portfolio_id)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return result


@router.post("/portfolios/{portfolio_id}/optimize")
async def optimize_portfolio(portfolio_id: str):
    """优化组合"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    result = manager.optimize_portfolio(portfolio_id)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.get("/portfolios/{portfolio_id}/positions")
async def get_portfolio_positions(portfolio_id: str):
    """获取组合仓位"""
    from deeppulse.strategy.portfolio import get_portfolio_manager

    manager = get_portfolio_manager()
    result = manager.calculate_position_sizes(portfolio_id)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return result
