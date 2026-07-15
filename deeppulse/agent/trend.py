"""趋势强度评估模块

综合评估股票的趋势方向、强度和阶段。
"""

import pandas as pd

from deeppulse.agent.indicators import TechnicalIndicators


def assess_trend(df: pd.DataFrame) -> dict:
    """综合评估趋势强度

    Args:
        df: 包含 OHLCV 的 DataFrame

    Returns:
        {
            "direction": "up" / "down" / "sideways",
            "direction_cn": "上涨" / "下跌" / "横盘",
            "strength": 0-100,
            "phase": "accumulation" / "markup" / "distribution" / "markdown",
            "signals": {...},
            "conclusion": str
        }
    """
    if len(df) < 60:
        return {"direction": "unknown", "strength": 0, "conclusion": "数据不足"}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    close.iloc[-1]

    scores = {}  # 各维度得分
    signals = {}

    # 1. 均线排列（权重 30%）
    ma_score, ma_signal = _score_ma_alignment(close)
    scores["ma"] = ma_score
    signals["ma_alignment"] = ma_signal

    # 2. ADX 趋势强度（权重 20%）
    adx_val = TechnicalIndicators.adx(high, low, close).iloc[-1]
    if pd.notna(adx_val):
        if adx_val > 40:
            scores["adx"] = 100
            signals["adx"] = f"强趋势 ({adx_val:.1f})"
        elif adx_val > 25:
            scores["adx"] = 60
            signals["adx"] = f"有趋势 ({adx_val:.1f})"
        else:
            scores["adx"] = 20
            signals["adx"] = f"无趋势/震荡 ({adx_val:.1f})"
    else:
        scores["adx"] = 50

    # 3. 价格动量（权重 20%）
    momentum_score, momentum_signal = _score_momentum(close)
    scores["momentum"] = momentum_score
    signals["momentum"] = momentum_signal

    # 4. 量能配合（权重 15%）
    volume_score, volume_signal = _score_volume(close, volume)
    scores["volume"] = volume_score
    signals["volume"] = volume_signal

    # 5. 价格位置（权重 15%）
    position_score, position_signal = _score_position(close)
    scores["position"] = position_score
    signals["position"] = position_signal

    # 综合评分
    weights = {"ma": 0.30, "adx": 0.20, "momentum": 0.20, "volume": 0.15, "position": 0.15}
    total_score = sum(scores.get(k, 50) * w for k, w in weights.items())
    total_score = max(0, min(100, total_score))

    # 判断方向
    if total_score >= 65:
        direction = "up"
        direction_cn = "上涨"
    elif total_score <= 35:
        direction = "down"
        direction_cn = "下跌"
    else:
        direction = "sideways"
        direction_cn = "横盘"

    # 判断阶段
    phase = _assess_phase(close, volume, total_score)

    # 生成结论
    conclusion = _generate_conclusion(direction_cn, total_score, phase, signals)

    return {
        "direction": direction,
        "direction_cn": direction_cn,
        "strength": round(total_score),
        "phase": phase,
        "phase_cn": _PHASE_CN.get(phase, phase),
        "signals": signals,
        "conclusion": conclusion,
    }


_PHASE_CN = {
    "accumulation": "吸筹期",
    "markup": "拉升期",
    "distribution": "派发期",
    "markdown": "下跌期",
}


def _score_ma_alignment(close: pd.Series) -> tuple[int, str]:
    """均线排列评分"""
    ma5 = TechnicalIndicators.ma(close, 5).iloc[-1]
    ma10 = TechnicalIndicators.ma(close, 10).iloc[-1]
    ma20 = TechnicalIndicators.ma(close, 20).iloc[-1]
    ma60 = TechnicalIndicators.ma(close, 60).iloc[-1]

    if not all(pd.notna(v) for v in [ma5, ma10, ma20, ma60]):
        return 50, "数据不足"

    if ma5 > ma10 > ma20 > ma60:
        return 90, "多头排列"
    elif ma5 > ma10 > ma20:
        return 75, "短中期多头"
    elif ma5 < ma10 < ma20 < ma60:
        return 10, "空头排列"
    elif ma5 < ma10 < ma20:
        return 25, "短中期空头"
    elif close.iloc[-1] > ma20:
        return 60, "站上20日线"
    else:
        return 40, "交叉整理"


def _score_momentum(close: pd.Series) -> tuple[int, str]:
    """动量评分"""
    pct_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) > 5 else 0
    pct_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 20 else 0

    score = 50
    if pct_5d > 5:
        score += 25
    elif pct_5d > 2:
        score += 15
    elif pct_5d < -5:
        score -= 25
    elif pct_5d < -2:
        score -= 15

    if pct_20d > 10:
        score += 25
    elif pct_20d > 5:
        score += 15
    elif pct_20d < -10:
        score -= 25
    elif pct_20d < -5:
        score -= 15

    score = max(0, min(100, score))

    signal = f"5日{pct_5d:+.1f}% 20日{pct_20d:+.1f}%"
    return score, signal


def _score_volume(close: pd.Series, volume: pd.Series) -> tuple[int, str]:
    """量能配合评分"""
    vol_ratio = volume.iloc[-1] / volume.tail(5).mean() if volume.tail(5).mean() > 0 else 1
    pct_change = (close.iloc[-1] / close.iloc[-2] - 1) if len(close) > 1 else 0

    # 量价同步 = 好
    if pct_change > 0 and vol_ratio > 1.5:
        return 80, "放量上涨"
    elif pct_change > 0 and vol_ratio < 0.7:
        return 50, "缩量上涨"
    elif pct_change < 0 and vol_ratio > 1.5:
        return 20, "放量下跌"
    elif pct_change < 0 and vol_ratio < 0.7:
        return 60, "缩量下跌（惜售）"
    else:
        return 50, f"量比 {vol_ratio:.1f}"


def _score_position(close: pd.Series) -> tuple[int, str]:
    """价格位置评分"""
    high_20d = close.tail(20).max()
    low_20d = close.tail(20).min()
    current = close.iloc[-1]

    if high_20d == low_20d:
        return 50, "无波动"

    position = (current - low_20d) / (high_20d - low_20d)

    if position > 0.9:
        return 70, "接近20日新高"
    elif position > 0.7:
        return 60, "20日高位区"
    elif position < 0.1:
        return 30, "接近20日新低"
    elif position < 0.3:
        return 40, "20日低位区"
    else:
        return 50, "20日中间位"


def _assess_phase(close: pd.Series, volume: pd.Series, score: float) -> str:
    """判断市场阶段"""
    vol_trend = volume.tail(10).mean() / volume.tail(30).mean() if volume.tail(30).mean() > 0 else 1
    close.iloc[-1] / close.iloc[-10] - 1 if len(close) > 10 else 0

    if score >= 65:
        if vol_trend > 1.2:
            return "markup"  # 拉升期：上涨 + 放量
        else:
            return "distribution"  # 派发期：上涨但量能不足
    elif score <= 35:
        if vol_trend > 1.2:
            return "markdown"  # 下跌期：下跌 + 放量
        else:
            return "accumulation"  # 吸筹期：下跌但缩量
    else:
        if vol_trend < 0.8:
            return "accumulation"  # 横盘缩量 = 吸筹
        else:
            return "distribution"


def _generate_conclusion(direction: str, score: float, phase: str, signals: dict) -> str:
    """生成趋势结论"""
    phase_cn = _PHASE_CN.get(phase, phase)
    parts = [f"当前为{direction}趋势（评分 {score:.0f}/100），处于{phase_cn}。"]

    ma = signals.get("ma_alignment", "")
    if "多头" in ma:
        parts.append("均线多头排列，趋势向好。")
    elif "空头" in ma:
        parts.append("均线空头排列，趋势偏弱。")

    vol = signals.get("volume", "")
    if "放量上涨" in vol:
        parts.append("量价配合良好。")
    elif "放量下跌" in vol:
        parts.append("注意放量下跌风险。")

    return "".join(parts)
