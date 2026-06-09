import json

from agent.tools.decorator import tool

# ============ 回测工具 ============


@tool(
    "对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤、夏普比率等指标。用于验证战法在历史数据上的表现。",
    param_desc=[
            "股票代码",
            "策略: ma_cross(macd_cross)/rsi_oversold/volume_breakout/boll_bounce/kdj_cross/multi_confirm",
            "回测天数（交易日）",
            "止损比例",
            "止盈比例",
            "最大持仓天数",
        ],
)
def backtest_stock(
    code: str,
    strategy: str = "macd_cross",
    days: int = 500,
    stop_loss: float = 0.05,
    take_profit: float = 0.10,
    max_hold: int = 20,
) -> str:
    """对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤等指标。

    Args:
        code: 股票代码
        strategy: 策略名 - ma_cross(均线金叉)/macd_cross(MACD金叉)/rsi_oversold(RSI超卖)/volume_breakout(放量突破)/boll_bounce(布林带反弹)/kdj_cross(KDJ金叉)/multi_confirm(多指标共振)
        days: 回测数据天数（交易日），默认500
        stop_loss: 止损比例，默认0.05(5%)
        take_profit: 止盈比例，默认0.10(10%)
        max_hold: 最大持仓天数，默认20
    """
    from agent.backtest import backtest_stock as _backtest_stock

    try:
        result = _backtest_stock(
            code,
            strategy=strategy,
            days=int(days),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            max_hold=int(max_hold),
        )
        return json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"回测失败: {e}"}, ensure_ascii=False)


@tool(
    "在多只股票上回测同一策略，汇总统计平均胜率、收益率、正收益占比。用于评估策略的普适性。",
    param_desc=[
            "策略名",
            "股票代码（逗号分隔），为空则随机抽样",
            "回测天数",
            "返回表现最好的N只",
        ],
)
def backtest_multi_stock(strategy: str = "macd_cross", codes: str = "", days: int = 500, top_n: int = 10) -> str:
    """在多只股票上回测同一策略，汇总统计胜率和收益率。

    Args:
        strategy: 策略名
        codes: 股票代码（逗号分隔），为空则随机抽样测试
        days: 回测天数
        top_n: 返回表现最好的N只
    """
    from agent.backtest import backtest_strategy_multi_stock

    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        result = backtest_strategy_multi_stock(strategy=strategy, codes=code_list, days=int(days), top_n=int(top_n))
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"批量回测失败: {e}"}, ensure_ascii=False)