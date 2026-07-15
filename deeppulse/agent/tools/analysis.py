"""分析工具 - 多周期数据、回测、K线形态、选股"""

import json

from deeppulse.agent.patterns import format_patterns as _format_patterns
from deeppulse.agent.patterns import recognize_patterns as _recognize_patterns
from deeppulse.agent.screener import screen_stocks as _screen_stocks
from deeppulse.agent.tools._shared import convert_volume_in_records, get_query


def recognize_kline_patterns(code: str, days: int = 60) -> str:
    """识别股票K线形态（十字星、锤子线、吞没、早晨之星等）"""
    from datetime import date, timedelta

    query = get_query()
    end = date.today()
    start = end - timedelta(days=int(days) * 3)
    df = query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无数据"}, ensure_ascii=False)

    df = df.sort_values("trade_date").reset_index(drop=True)
    patterns = _recognize_patterns(df)
    formatted = _format_patterns(patterns)
    return json.dumps({"code": code, "patterns": patterns, "summary": formatted}, ensure_ascii=False, default=str)


def screen_stocks(conditions: str, limit: int = 20) -> str:
    """根据技术条件筛选股票（支持MA、RSI、MACD、成交量、涨跌幅等条件）"""
    result = _screen_stocks(conditions, limit)
    return result


def update_timeframe_data(code: str, timeframe: str = "5m", days: int = 5) -> str:
    """更新股票的多周期K线数据（分钟级/周线）。"""
    from deeppulse.agent.timeframes import update_timeframe

    result = update_timeframe(code, timeframe, int(days))
    return json.dumps(result, ensure_ascii=False, default=str)


def query_timeframe_kline(code: str, timeframe: str = "5m", limit: int = 100) -> str:
    """查询股票多周期K线数据。"""
    from deeppulse.agent.timeframes import query_timeframe

    result = query_timeframe(code, timeframe, int(limit))
    # 成交量：股→手
    if isinstance(result.get("kline"), list):
        convert_volume_in_records(result["kline"])
    result["volume_unit"] = "手"
    return json.dumps(result, ensure_ascii=False, default=str)


def multi_timeframe_analysis(code: str, limit: int = 50) -> str:
    """获取多周期K线数据（日线+60分钟+15分钟），用于多周期共振分析。"""
    from deeppulse.agent.timeframes import get_multi_timeframe_data

    result = get_multi_timeframe_data(code, ["daily", "60m", "15m"], int(limit))
    # 成交量：股→手（遍历每个周期的数据）
    if isinstance(result.get("timeframes"), dict):
        for tf_data in result["timeframes"].values():
            if isinstance(tf_data.get("kline"), list):
                convert_volume_in_records(tf_data["kline"])
    result["volume_unit"] = "手"
    return json.dumps(result, ensure_ascii=False, default=str)


def backtest_stock(
    code: str,
    strategy: str = "macd_cross",
    days: int = 500,
    stop_loss: float = 0.05,
    take_profit: float = 0.10,
    max_hold: int = 20,
) -> str:
    """对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤等指标。"""
    from deeppulse.agent.backtest import backtest_stock as _backtest_stock

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


def backtest_multi_stock(strategy: str = "macd_cross", codes: str = "", days: int = 500, top_n: int = 10) -> str:
    """在多只股票上回测同一策略，汇总统计胜率和收益率。"""
    from deeppulse.agent.backtest import backtest_strategy_multi_stock

    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        result = backtest_strategy_multi_stock(strategy=strategy, codes=code_list, days=int(days), top_n=int(top_n))
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"批量回测失败: {e}"}, ensure_ascii=False)


def generate_chart(code: str, days: int = 120, show_macd: bool = True, show_rsi: bool = False) -> str:
    """生成K线图（带均线、MACD等技术指标），保存到本地文件。"""
    # Web 模式下跳过图表生成，前端已有交互式图表
    import os
    if os.environ.get("DEEPPULSE_WEB_MODE"):
        return json.dumps({
            "status": "skipped",
            "message": f"K线图已在前端交互式展示，无需生成静态文件。请使用「分析」页面查看 {code} 的K线图。"
        }, ensure_ascii=False)

    from deeppulse.agent.charts import generate_kline_chart

    try:
        path = generate_kline_chart(code, days=int(days), show_macd=show_macd, show_rsi=show_rsi, auto_open=False)
        return json.dumps({"status": "ok", "path": path, "message": f"K线图已保存到: {path}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"生成图表失败: {e}"}, ensure_ascii=False)


def compare_stocks_chart(codes: str, days: int = 60) -> str:
    """生成多只股票涨幅对比图（归一化），保存到本地文件。"""
    # Web 模式下跳过图表生成
    import os
    if os.environ.get("DEEPPULSE_WEB_MODE"):
        return json.dumps({
            "status": "skipped",
            "message": f"对比图已在前端交互式展示，无需生成静态文件。"
        }, ensure_ascii=False)

    from deeppulse.agent.charts import generate_comparison_chart

    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        path = generate_comparison_chart(code_list, days=int(days), auto_open=False)
        return json.dumps({"status": "ok", "path": path, "message": f"对比图已保存到: {path}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"生成图表失败: {e}"}, ensure_ascii=False)


# ── 新增分析工具 ──────────────────────────────────────


def detect_divergence(code: str, indicator: str = "rsi", days: int = 60) -> str:
    """检测价格与技术指标的背离信号（底背离/顶背离），是高价值的反转预警。"""
    from datetime import date, timedelta
    from deeppulse.agent.divergence import detect_all_divergences, detect_divergence as _detect

    query = get_query()
    end = date.today()
    start = end - timedelta(days=int(days) * 3)
    df = query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无数据"}, ensure_ascii=False)

    df = df.sort_values("trade_date").reset_index(drop=True)

    if indicator == "all":
        results = detect_all_divergences(df, lookback=min(int(days), 30))
    else:
        results = _detect(df, indicator=indicator, lookback=min(int(days), 30))

    return json.dumps({"code": code, "divergences": results, "count": len(results)}, ensure_ascii=False, default=str)


def detect_support_resistance(code: str, days: int = 60) -> str:
    """自动检测股票的关键支撑位和压力位（前高前低、均线、布林带、整数关口）。"""
    from datetime import date, timedelta
    from deeppulse.agent.support_resistance import detect_support_resistance as _detect

    query = get_query()
    end = date.today()
    start = end - timedelta(days=int(days) * 3)
    df = query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无数据"}, ensure_ascii=False)

    df = df.sort_values("trade_date").reset_index(drop=True)
    result = _detect(df)
    result["code"] = code
    return json.dumps(result, ensure_ascii=False, default=str)


def assess_trend(code: str, days: int = 60) -> str:
    """综合评估股票的趋势方向、强度和阶段（吸筹/拉升/派发/下跌），给出0-100评分。"""
    from datetime import date, timedelta
    from deeppulse.agent.trend import assess_trend as _assess

    query = get_query()
    end = date.today()
    start = end - timedelta(days=int(days) * 3)
    df = query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无数据"}, ensure_ascii=False)

    df = df.sort_values("trade_date").reset_index(drop=True)
    result = _assess(df)
    result["code"] = code
    return json.dumps(result, ensure_ascii=False, default=str)


def analyze_volume_price(code: str, days: int = 60) -> str:
    """深度量价分析：量能趋势、量价同步性、异常量能检测、量价背离。"""
    from datetime import date, timedelta
    from deeppulse.agent.volume_analysis import analyze_volume_price as _analyze

    query = get_query()
    end = date.today()
    start = end - timedelta(days=int(days) * 3)
    df = query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无数据"}, ensure_ascii=False)

    df = df.sort_values("trade_date").reset_index(drop=True)
    result = _analyze(df)
    result["code"] = code
    return json.dumps(result, ensure_ascii=False, default=str)


def analyze_confluence(code: str) -> str:
    """多周期共振分析：分析日线+60分钟+15分钟的信号一致性，给出共振评分。"""
    from deeppulse.agent.timeframe_confluence import analyze_confluence as _analyze
    return _analyze(code)


def screen_stocks_v2(conditions: str, limit: int = 20) -> str:
    """高性能选股器V2：支持组合条件选股，使用DuckDB批量计算。"""
    from deeppulse.agent.screener_v2 import StockScreener

    screener = StockScreener()

    # 解析条件字符串为结构化条件
    cond_list = _parse_conditions(conditions)
    return screener.screen(cond_list, limit=int(limit))


def _parse_conditions(conditions: str) -> list:
    """解析条件字符串"""
    cond_list = []
    parts = [p.strip() for p in conditions.replace("，", ",").replace("且", ",").split(",") if p.strip()]

    for part in parts:
        part = part.strip()

        if "多头排列" in part:
            cond_list.append({"indicator": "ma_alignment", "type": "bullish"})
        elif "空头排列" in part:
            cond_list.append({"indicator": "ma_alignment", "type": "bearish"})
        elif "MACD金叉" in part or "macd金叉" in part:
            cond_list.append({"indicator": "macd", "type": "golden_cross"})
        elif "MACD死叉" in part or "macd死叉" in part:
            cond_list.append({"indicator": "macd", "type": "death_cross"})
        elif "放量" in part:
            cond_list.append({"indicator": "volume_ratio", "op": ">", "value": 2.0})
        elif "缩量" in part:
            cond_list.append({"indicator": "volume_ratio", "op": "<", "value": 0.7})

        # 数值条件
        import re
        m = re.search(r"MA(\d+)\s*>\s*MA(\d+)", part)
        if m:
            cond_list.append({"indicator": "ma_cross", "params": {"fast": int(m.group(1)), "slow": int(m.group(2))}})
            continue

        m = re.search(r"RSI\s*(\d*)\s*([<>]=?)\s*(\d+)", part, re.IGNORECASE)
        if m:
            cond_list.append({"indicator": "rsi", "op": m.group(2), "value": float(m.group(3))})
            continue

        m = re.search(r"涨幅\s*>\s*([\d.]+)", part)
        if m:
            cond_list.append({"indicator": "pct_change", "op": ">", "value": float(m.group(1))})
            continue

        m = re.search(r"量比\s*>\s*([\d.]+)", part)
        if m:
            cond_list.append({"indicator": "volume_ratio", "op": ">", "value": float(m.group(1))})
            continue

    return cond_list if cond_list else [{"indicator": "pct_change", "op": ">", "value": 0}]


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "recognize_kline_patterns",
            "description": "识别股票K线形态（十字星、锤子线、吞没、早晨之星等），返回识别到的形态列表和综合判断。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码"},
                    "days": {"type": "integer", "description": "分析的交易日数量，默认60", "default": 60},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_stocks",
            "description": "根据技术条件筛选股票（支持MA、RSI、MACD、成交量、涨跌幅等条件）",
            "parameters": {
                "type": "object",
                "properties": {
                    "conditions": {
                        "type": "string",
                        "description": "筛选条件，如 'MA5>MA10', 'RSI6<20', 'MACD金叉', '放量', '涨幅>3%', '多头排列'",
                    },
                    "limit": {"type": "integer", "description": "最多返回数量，默认20", "default": 20},
                },
                "required": ["conditions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_timeframe_data",
            "description": "更新股票的多周期K线数据（分钟级/周线）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "timeframe": {
                        "type": "string",
                        "description": "周期 - 1m(1分钟)/5m(5分钟)/15m(15分钟)/30m(30分钟)/60m(60分钟)/weekly(周线)",
                        "default": "5m",
                    },
                    "days": {"type": "integer", "description": "分钟数据天数，默认5", "default": 5},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_timeframe_kline",
            "description": "查询股票多周期K线数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "timeframe": {"type": "string", "description": "周期 - 1m/5m/15m/30m/60m/weekly", "default": "5m"},
                    "limit": {"type": "integer", "description": "返回条数，默认100", "default": 100},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_timeframe_analysis",
            "description": "获取多周期K线数据（日线+60分钟+15分钟），用于多周期共振分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "limit": {"type": "integer", "description": "每个周期返回条数，默认50", "default": 50},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_stock",
            "description": "对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤等指标。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "strategy": {
                        "type": "string",
                        "description": "策略名 - ma_cross(均线金叉)/macd_cross(MACD金叉)/rsi_oversold(RSI超卖)/volume_breakout(放量突破)/boll_bounce(布林带反弹)/kdj_cross(KDJ金叉)/multi_confirm(多指标共振)",
                        "default": "macd_cross",
                    },
                    "days": {"type": "integer", "description": "回测数据天数（交易日），默认500", "default": 500},
                    "stop_loss": {"type": "number", "description": "止损比例，默认0.05(5%)", "default": 0.05},
                    "take_profit": {"type": "number", "description": "止盈比例，默认0.10(10%)", "default": 0.10},
                    "max_hold": {"type": "integer", "description": "最大持仓天数，默认20", "default": 20},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_multi_stock",
            "description": "在多只股票上回测同一策略，汇总统计胜率和收益率。",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy": {"type": "string", "description": "策略名", "default": "macd_cross"},
                    "codes": {
                        "type": "string",
                        "description": "股票代码（逗号分隔），为空则随机抽样测试",
                        "default": "",
                    },
                    "days": {"type": "integer", "description": "回测天数", "default": 500},
                    "top_n": {"type": "integer", "description": "返回表现最好的N只", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": "生成K线图（带均线、MACD等技术指标），保存到本地文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "days": {"type": "integer", "description": "显示天数（交易日），默认120", "default": 120},
                    "show_macd": {"type": "boolean", "description": "是否显示MACD，默认true", "default": True},
                    "show_rsi": {"type": "boolean", "description": "是否显示RSI，默认false", "default": False},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_stocks_chart",
            "description": "生成多只股票涨幅对比图（归一化），保存到本地文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "codes": {"type": "string", "description": "股票代码，逗号分隔（如 '600519,000858,000568'）"},
                    "days": {"type": "integer", "description": "对比天数，默认60", "default": 60},
                },
                "required": ["codes"],
            },
        },
    },
    # ── 新增分析工具定义 ──
    {
        "type": "function",
        "function": {
            "name": "detect_divergence",
            "description": "检测价格与技术指标的背离信号（底背离/顶背离），是高价值的反转预警。支持RSI、MACD、KDJ背离检测。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码"},
                    "indicator": {"type": "string", "description": "指标类型: rsi/macd/kdj/all", "default": "all"},
                    "days": {"type": "integer", "description": "分析天数，默认60", "default": 60},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_support_resistance",
            "description": "自动检测股票的关键支撑位和压力位（前高前低、均线、布林带、整数关口），判断当前位置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码"},
                    "days": {"type": "integer", "description": "分析天数，默认60", "default": 60},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_trend",
            "description": "综合评估股票的趋势方向、强度和阶段（吸筹/拉升/派发/下跌），给出0-100评分和多维度信号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码"},
                    "days": {"type": "integer", "description": "分析天数，默认60", "default": 60},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_volume_price",
            "description": "深度量价分析：量能趋势、量价同步性、异常量能检测、量价背离，判断主力资金动向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码"},
                    "days": {"type": "integer", "description": "分析天数，默认60", "default": 60},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_confluence",
            "description": "多周期共振分析：分析日线+60分钟+15分钟的信号一致性，给出共振评分和冲突提示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6位股票代码"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_stocks_v2",
            "description": "高性能选股器V2：支持组合条件选股（如'多头排列,放量,涨幅>3%'），使用DuckDB批量计算。",
            "parameters": {
                "type": "object",
                "properties": {
                    "conditions": {"type": "string", "description": "筛选条件，逗号分隔"},
                    "limit": {"type": "integer", "description": "最多返回数量，默认20", "default": 20},
                },
                "required": ["conditions"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "recognize_kline_patterns": recognize_kline_patterns,
    "screen_stocks": screen_stocks,
    "update_timeframe_data": update_timeframe_data,
    "query_timeframe_kline": query_timeframe_kline,
    "multi_timeframe_analysis": multi_timeframe_analysis,
    "backtest_stock": backtest_stock,
    "backtest_multi_stock": backtest_multi_stock,
    "generate_chart": generate_chart,
    "compare_stocks_chart": compare_stocks_chart,
    # 新增分析工具
    "detect_divergence": detect_divergence,
    "detect_support_resistance": detect_support_resistance,
    "assess_trend": assess_trend,
    "analyze_volume_price": analyze_volume_price,
    "analyze_confluence": analyze_confluence,
    "screen_stocks_v2": screen_stocks_v2,
}
