"""支撑压力位检测模块

自动识别关键支撑位和压力位，基于前高前低、均线、布林带、整数关口等。
"""

import pandas as pd

from deeppulse.agent.indicators import TechnicalIndicators


def detect_support_resistance(df: pd.DataFrame) -> dict:
    """自动检测关键支撑压力位

    Args:
        df: 包含 OHLCV 的 DataFrame

    Returns:
        {
            "resistance": [{"price": float, "type": str, "strength": int}],
            "support": [{"price": float, "type": str, "strength": int}],
            "current_price": float,
            "position": str
        }
    """
    if len(df) < 20:
        return {"resistance": [], "support": [], "current_price": 0, "position": "数据不足"}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    current = float(close.iloc[-1])

    resistance = []
    support = []

    # 1. 前高前低（局部极值点）
    highs = _find_pivots(high, window=5, kind="high")
    lows = _find_pivots(low, window=5, kind="low")

    for h in highs:
        if h > current:
            resistance.append({"price": round(h, 2), "type": "前高", "strength": 3, "source": "pivot"})
        elif h < current * 1.02:
            support.append({"price": round(h, 2), "type": "前高转支撑", "strength": 2, "source": "pivot"})

    for l in lows:
        if l < current:
            support.append({"price": round(l, 2), "type": "前低", "strength": 3, "source": "pivot"})
        elif l > current * 0.98:
            resistance.append({"price": round(l, 2), "type": "前低转压力", "strength": 2, "source": "pivot"})

    # 2. 均线位置
    for period in [5, 10, 20, 60]:
        ma_val = TechnicalIndicators.ma(close, period).iloc[-1]
        if pd.notna(ma_val):
            ma_val = float(ma_val)
            if ma_val > current * 1.005:
                resistance.append({"price": round(ma_val, 2), "type": f"MA{period}", "strength": 2, "source": "ma"})
            elif ma_val < current * 0.995:
                support.append({"price": round(ma_val, 2), "type": f"MA{period}", "strength": 2, "source": "ma"})

    # 3. 布林带
    upper, mid, lower = TechnicalIndicators.boll(close)
    if pd.notna(upper.iloc[-1]):
        if float(upper.iloc[-1]) > current:
            resistance.append(
                {"price": round(float(upper.iloc[-1]), 2), "type": "布林上轨", "strength": 2, "source": "boll"}
            )
        if float(lower.iloc[-1]) < current:
            support.append(
                {"price": round(float(lower.iloc[-1]), 2), "type": "布林下轨", "strength": 2, "source": "boll"}
            )
        if float(mid.iloc[-1]) > current:
            resistance.append(
                {"price": round(float(mid.iloc[-1]), 2), "type": "布林中轨", "strength": 1, "source": "boll"}
            )
        elif float(mid.iloc[-1]) < current:
            support.append(
                {"price": round(float(mid.iloc[-1]), 2), "type": "布林中轨", "strength": 1, "source": "boll"}
            )

    # 4. 整数关口
    for base in [50, 100, 200, 500, 1000, 2000, 5000]:
        round_price = round(current / base) * base
        if round_price > current and round_price < current * 1.1:
            resistance.append({"price": round_price, "type": "整数关口", "strength": 1, "source": "round"})
        elif round_price < current and round_price > current * 0.9:
            support.append({"price": round_price, "type": "整数关口", "strength": 1, "source": "round"})

    # 去重并排序
    resistance = _deduplicate(resistance)
    support = _deduplicate(support)
    resistance.sort(key=lambda x: x["price"])
    support.sort(key=lambda x: x["price"], reverse=True)

    # 限制数量
    resistance = resistance[:5]
    support = support[:5]

    # 判断当前位置
    position = _assess_position(current, resistance, support)

    return {
        "resistance": resistance,
        "support": support,
        "current_price": round(current, 2),
        "position": position,
    }


def _find_pivots(series: pd.Series, window: int = 5, kind: str = "high") -> list[float]:
    """查找枢轴点（局部极值）"""
    values = series.values
    pivots = []
    for i in range(window, len(values) - window):
        if kind == "high":
            if all(values[i] >= values[i - j] for j in range(1, window + 1)) and all(
                values[i] >= values[i + j] for j in range(1, min(window + 1, len(values) - i))
            ):
                pivots.append(float(values[i]))
        else:
            if all(values[i] <= values[i - j] for j in range(1, window + 1)) and all(
                values[i] <= values[i + j] for j in range(1, min(window + 1, len(values) - i))
            ):
                pivots.append(float(values[i]))
    return pivots


def _deduplicate(levels: list[dict], threshold: float = 0.01) -> list[dict]:
    """去重相近的支撑压力位"""
    if not levels:
        return []
    levels.sort(key=lambda x: x["price"])
    result = [levels[0]]
    for level in levels[1:]:
        if abs(level["price"] - result[-1]["price"]) / result[-1]["price"] > threshold:
            result.append(level)
        else:
            # 取强度更高的
            if level["strength"] > result[-1]["strength"]:
                result[-1] = level
    return result


def _assess_position(current: float, resistance: list, support: list) -> str:
    """评估当前位置"""
    if not resistance and not support:
        return "无法判断"

    nearest_res = resistance[0]["price"] if resistance else float("inf")
    nearest_sup = support[0]["price"] if support else 0

    res_dist = (nearest_res - current) / current if current else 0
    sup_dist = (current - nearest_sup) / current if current else 0

    if res_dist < 0.02:
        return f"靠近压力位 {nearest_res}（距离 {res_dist:.1%}）"
    elif sup_dist < 0.02:
        return f"靠近支撑位 {nearest_sup}（距离 {sup_dist:.1%}）"
    elif res_dist < sup_dist:
        return f"偏向上方压力位 {nearest_res}（距离 {res_dist:.1%}）"
    else:
        return f"偏向上方支撑位 {nearest_sup}（距离 {sup_dist:.1%}）"
