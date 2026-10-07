"""策略参数网格搜索 - 纯计算，无 LLM

保留网格搜索纯函数，供 calc_engine 的 calibrate_params 工具取用。
原 LLM 自动优化循环（AutoOptimizer/OptimizeReport）已随策略平台降级移除。
"""

from deeppulse.strategy.backtest import backtest_stock
from deeppulse.strategy.factor_library import get_factor
from deeppulse.strategy.models import BacktestResult
from deeppulse.strategy.spec import StrategySpec, compile_signal_func

# 每轮回测次数上限：避免参数组合爆炸
MAX_BACKTESTS_PER_ROUND = 50


def compute_score(result: BacktestResult) -> float:
    """综合评分：收益×0.4 + 夏普×0.03 + 胜率×0.2 − 回撤×0.1"""
    return result.total_return * 0.4 + result.sharpe_ratio * 0.03 + result.win_rate * 0.2 - result.max_drawdown * 0.1


def run_spec_backtest(code: str, spec: StrategySpec, days: int) -> tuple[BacktestResult, float]:
    """回测一个策略规格，返回 (结果, 综合评分)。纯计算，无 LLM。"""
    signal_func = compile_signal_func(spec)
    result = backtest_stock(
        code,
        signal_func=signal_func,
        days=days,
        exit_strategy_preset=spec.exit_preset,
        exit_strategy_params=spec.exit_params or None,
    )
    result.strategy_name = spec.name
    return result, compute_score(result)


def grid_search_params(
    code: str, spec: StrategySpec, days: int, base_score: float, budget: int = MAX_BACKTESTS_PER_ROUND
) -> tuple[StrategySpec, BacktestResult | None, float, list[dict]]:
    """单参数扰动式网格搜索：一次只动一个参数，贪心接受更优解。纯计算，无 LLM。

    返回 (best_spec, best_result, best_score, surface)；surface 为已测组合的评分面。
    """
    best_spec = StrategySpec.from_dict(spec.to_dict())
    best_result: BacktestResult | None = None
    best_score = base_score
    surface: list[dict] = []
    budget = max(1, int(budget))

    for factor_idx, fspec in enumerate(best_spec.factors):
        try:
            factor = get_factor(fspec.factor_id)
        except KeyError:
            continue
        for pname, fparam in factor.params.items():
            current = float(fspec.params.get(pname, fparam.default))
            candidates = [v for v in fparam.grid() if abs(v - current) > 1e-9]
            for value in candidates:
                if budget <= 0:
                    return best_spec, best_result, best_score, surface
                trial = StrategySpec.from_dict(best_spec.to_dict())
                trial.factors[factor_idx].params[pname] = value
                if trial.validate():
                    continue
                budget -= 1
                try:
                    result, score = run_spec_backtest(code, trial, days)
                except Exception:
                    continue
                surface.append(
                    {
                        "factor_id": fspec.factor_id,
                        "param": pname,
                        "value": value,
                        "score": round(score, 4),
                        "win_rate": round(result.win_rate, 3),
                        "max_drawdown": round(result.max_drawdown, 4),
                        "total_trades": result.total_trades,
                    }
                )
                if score > best_score:
                    best_spec, best_result, best_score = trial, result, score
    return best_spec, best_result, best_score, surface
