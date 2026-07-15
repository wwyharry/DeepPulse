"""多周期共振分析模块

分析日线 + 60分钟 + 15分钟的信号一致性，给出共振评分。
"""

import json

import pandas as pd

from deeppulse.agent.indicators import TechnicalIndicators


def analyze_confluence(code: str) -> str:
    """多周期共振分析

    Returns:
        JSON 格式的共振分析结果
    """
    from datetime import date, timedelta
    from deeppulse.src.query import StockQuery

    query = StockQuery()

    # 获取多周期数据
    end = date.today()
    start = end - timedelta(days=180)
    daily = query.get_daily_kline(code, start_date=str(start), end_date=str(end))

    if daily.empty or len(daily) < 60:
        return json.dumps({"error": "日线数据不足"}, ensure_ascii=False)

    # 分析各周期
    daily_analysis = _analyze_single_timeframe(daily, "日线")

    # 尝试获取分钟数据
    try:
        from deeppulse.agent.timeframes import query_timeframe
        hourly = query_timeframe(code, timeframe="60m", limit=120)
        if hourly is not None and not hourly.empty and len(hourly) > 30:
            hourly_analysis = _analyze_single_timeframe(hourly, "60分钟")
        else:
            hourly_analysis = None
    except Exception:
        hourly_analysis = None

    try:
        from deeppulse.agent.timeframes import query_timeframe
        min15 = query_timeframe(code, timeframe="15m", limit=120)
        if min15 is not None and not min15.empty and len(min15) > 30:
            min15_analysis = _analyze_single_timeframe(min15, "15分钟")
        else:
            min15_analysis = None
    except Exception:
        min15_analysis = None

    # 计算共振评分
    all_analyses = [a for a in [daily_analysis, hourly_analysis, min15_analysis] if a is not None]
    confluence_score = _calculate_confluence_score(all_analyses)

    # 检测冲突
    conflicts = _detect_conflicts(all_analyses)

    # 生成结论
    conclusion = _generate_conclusion(confluence_score, all_analyses, conflicts)

    result = {
        "code": code,
        "confluence_score": confluence_score,
        "timeframes": {
            "daily": daily_analysis,
            "hourly": hourly_analysis,
            "quarter_hourly": min15_analysis,
        },
        "conflicts": conflicts,
        "conclusion": conclusion,
    }

    return json.dumps(result, ensure_ascii=False, default=str)


def _analyze_single_timeframe(df: pd.DataFrame, label: str) -> dict:
    """分析单个周期"""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # 计算指标
    ma5 = TechnicalIndicators.ma(close, 5).iloc[-1]
    ma10 = TechnicalIndicators.ma(close, 10).iloc[-1]
    ma20 = TechnicalIndicators.ma(close, 20).iloc[-1]
    dif, dea, macd = TechnicalIndicators.macd(close)
    rsi = TechnicalIndicators.rsi(close, 14).iloc[-1]

    # 趋势判断
    if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20):
        if ma5 > ma10 > ma20:
            trend = "up"
            trend_cn = "上涨"
            ma_signal = "多头排列"
        elif ma5 < ma10 < ma20:
            trend = "down"
            trend_cn = "下跌"
            ma_signal = "空头排列"
        else:
            trend = "sideways"
            trend_cn = "横盘"
            ma_signal = "交叉整理"
    else:
        trend = "unknown"
        trend_cn = "未知"
        ma_signal = "数据不足"

    # MACD 信号
    if pd.notna(dif.iloc[-1]) and pd.notna(dea.iloc[-1]):
        if dif.iloc[-1] > dea.iloc[-1]:
            macd_signal = "多头"
        else:
            macd_signal = "空头"
    else:
        macd_signal = "未知"

    # RSI 信号
    if pd.notna(rsi):
        if rsi > 70:
            rsi_signal = "超买"
        elif rsi < 30:
            rsi_signal = "超卖"
        else:
            rsi_signal = "中性"
    else:
        rsi_signal = "未知"

    # 综合得分
    score = 50
    if trend == "up":
        score += 20
    elif trend == "down":
        score -= 20
    if macd_signal == "多头":
        score += 15
    elif macd_signal == "空头":
        score -= 15
    if rsi_signal == "超买":
        score -= 10
    elif rsi_signal == "超卖":
        score += 10

    score = max(0, min(100, score))

    return {
        "label": label,
        "trend": trend,
        "trend_cn": trend_cn,
        "score": score,
        "ma_alignment": ma_signal,
        "macd": macd_signal,
        "rsi": rsi_signal,
        "rsi_value": round(float(rsi), 1) if pd.notna(rsi) else None,
    }


def _calculate_confluence_score(analyses: list[dict]) -> int:
    """计算共振评分"""
    if not analyses:
        return 0

    # 平均分
    avg_score = sum(a["score"] for a in analyses) / len(analyses)

    # 一致性加成
    trends = [a["trend"] for a in analyses]
    if len(set(trends)) == 1:
        # 所有周期趋势一致
        avg_score += 15
    elif len(set(trends)) == len(trends):
        # 所有周期趋势不一致
        avg_score -= 15

    return max(0, min(100, round(avg_score)))


def _detect_conflicts(analyses: list[dict]) -> list[str]:
    """检测周期间的冲突"""
    conflicts = []

    if len(analyses) < 2:
        return conflicts

    # 检查趋势冲突
    trends = [a["trend"] for a in analyses]
    labels = [a["label"] for a in analyses]

    for i in range(len(analyses)):
        for j in range(i + 1, len(analyses)):
            if trends[i] != trends[j] and "unknown" not in [trends[i], trends[j]]:
                conflicts.append(f"{labels[i]}{analyses[i]['trend_cn']}但{labels[j]}{analyses[j]['trend_cn']}")

    return conflicts


def _generate_conclusion(score: int, analyses: list[dict], conflicts: list[str]) -> str:
    """生成结论"""
    parts = []

    if score >= 75:
        parts.append(f"多周期共振良好（评分 {score}/100），趋势一致性高。")
    elif score >= 50:
        parts.append(f"多周期信号中性（评分 {score}/100），存在一定分歧。")
    else:
        parts.append(f"多周期信号偏弱（评分 {score}/100），趋势不一致。")

    if analyses:
        daily = next((a for a in analyses if a["label"] == "日线"), None)
        if daily:
            parts.append(f"日线趋势{daily['trend_cn']}，{daily['ma_alignment']}。")

    if conflicts:
        parts.append(f"⚠️ 周期冲突：{'；'.join(conflicts)}。")
        parts.append("建议等待短期周期与长期周期方向一致后再操作。")

    return "".join(parts)
