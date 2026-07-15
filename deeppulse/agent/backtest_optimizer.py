"""回测参数优化器

网格搜索最优参数组合，支持基准对比。
"""

import json
from itertools import product

import numpy as np
import pandas as pd

from deeppulse.agent.backtest import BacktestEngine, BacktestResult


def optimize_strategy(code: str, strategy_name: str, param_grid: dict, days: int = 250) -> str:
    """网格搜索最优参数

    Args:
        code: 股票代码
        strategy_name: 策略名称
        param_grid: 参数网格 {"fast": [5, 10], "slow": [20, 30]}
        days: 回测天数

    Returns:
        JSON 格式的优化结果
    """
    from deeppulse.agent.backtest import backtest_stock as _backtest_stock

    # 生成参数组合
    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())
    combinations = list(product(*param_values))

    results = []
    for combo in combinations:
        params = dict(zip(param_names, combo))
        try:
            result = _backtest_stock(code, strategy=strategy_name, days=days, **params)
            # 处理 BacktestResult 对象或 dict
            if hasattr(result, 'to_dict'):
                d = result.to_dict()
            else:
                d = result
            results.append({
                "params": params,
                "total_return": round(d.get("total_return", 0), 4),
                "annual_return": round(d.get("annual_return", 0), 4),
                "max_drawdown": round(d.get("max_drawdown", 0), 4),
                "win_rate": round(d.get("win_rate", 0), 3),
                "sharpe_ratio": round(d.get("sharpe_ratio", 0), 2),
                "total_trades": d.get("total_trades", 0),
                "profit_factor": round(d.get("profit_factor", 0), 2),
            })
        except Exception:
            continue

    if not results:
        return json.dumps({"error": "所有参数组合都失败"}, ensure_ascii=False)

    # 按综合评分排序
    for r in results:
        # 综合评分 = 收益 * 0.4 + 夏普 * 0.3 + 胜率 * 0.2 - 回撤 * 0.1
        r["score"] = (
            r["total_return"] * 0.4 +
            r["sharpe_ratio"] * 0.03 +
            r["win_rate"] * 0.2 -
            abs(r["max_drawdown"]) * 0.1
        )
    results.sort(key=lambda x: x["score"], reverse=True)

    return json.dumps({
        "code": code,
        "strategy": strategy_name,
        "total_combinations": len(combinations),
        "valid_results": len(results),
        "best_params": results[0]["params"],
        "best_result": results[0],
        "top_5": results[:5],
    }, ensure_ascii=False)


def backtest_with_benchmark(code: str, strategy_name: str, benchmark: str = "000300", days: int = 250, **kwargs) -> str:
    """回测并对比基准收益

    Args:
        code: 股票代码
        strategy_name: 策略名称
        benchmark: 基准指数代码（默认沪深300）
        days: 回测天数
    """
    from deeppulse.agent.backtest import backtest_stock as _backtest_stock

    # 运行回测
    result = _backtest_stock(code, strategy=strategy_name, days=days, **kwargs)

    response = {
        "code": code,
        "strategy": strategy_name,
        "period": f"{result.start_date} ~ {result.end_date}",
        "strategy_return": round(result.total_return, 4),
        "annual_return": round(result.annual_return, 4),
        "max_drawdown": round(result.max_drawdown, 4),
        "win_rate": round(result.win_rate, 3),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "total_trades": result.total_trades,
        "profit_factor": round(result.profit_factor, 2),
        "summary": result.summary(),
    }

    return json.dumps(response, ensure_ascii=False)


def monte_carlo_simulation(total_return: float, trades: list, n_simulations: int = 1000) -> str:
    """蒙特卡洛模拟

    基于历史交易的收益率分布，模拟未来可能的结果。
    """
    if not trades:
        return json.dumps({"error": "无交易记录"}, ensure_ascii=False)

    # 提取每笔交易的收益率
    returns = [t.get("pnl_pct", 0) for t in trades if "pnl_pct" in t]
    if not returns:
        return json.dumps({"error": "无有效收益率数据"}, ensure_ascii=False)

    returns = np.array(returns)

    # 模拟
    final_values = []
    for _ in range(n_simulations):
        # 随机打乱交易顺序
        shuffled = np.random.choice(returns, size=len(returns), replace=True)
        # 复合收益
        cumulative = np.prod(1 + shuffled)
        final_values.append(cumulative)

    final_values = np.array(final_values)

    # 统计结果
    percentiles = np.percentile(final_values, [5, 25, 50, 75, 95])

    return json.dumps({
        "n_simulations": n_simulations,
        "n_trades": len(returns),
        "actual_return": round(total_return, 4),
        "median_final": round(float(percentiles[2]), 4),
        "percentile_5": round(float(percentiles[0]), 4),
        "percentile_25": round(float(percentiles[1]), 4),
        "percentile_75": round(float(percentiles[3]), 4),
        "percentile_95": round(float(percentiles[4]), 4),
        "prob_profit": round(float(np.mean(final_values > 1)), 3),
        "prob_loss_10pct": round(float(np.mean(final_values < 0.9)), 3),
        "prob_double": round(float(np.mean(final_values > 2)), 3),
        "var_95": round(float(1 - percentiles[0]), 4),
    }, ensure_ascii=False)
