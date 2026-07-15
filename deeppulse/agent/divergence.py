"""背离检测模块

检测价格与技术指标之间的背离信号，是高价值的反转预警。
"""

import numpy as np
import pandas as pd

from deeppulse.agent.indicators import TechnicalIndicators


def detect_divergence(df: pd.DataFrame, indicator: str = "rsi", lookback: int = 20) -> list[dict]:
    """检测价格与指标的背离

    Args:
        df: 包含 OHLCV 的 DataFrame
        indicator: 指标类型 "rsi" / "macd" / "kdj"
        lookback: 回看周期

    Returns:
        背离信号列表
    """
    if len(df) < lookback + 5:
        return []

    close = df["close"]

    # 计算指标值
    if indicator == "rsi":
        ind_values = TechnicalIndicators.rsi(close, 14)
    elif indicator == "macd":
        dif, dea, macd = TechnicalIndicators.macd(close)
        ind_values = macd
    elif indicator == "kdj":
        k, d, j = TechnicalIndicators.kdj(df["high"], df["low"], close)
        ind_values = j
    else:
        return []

    results = []
    recent = df.tail(lookback).copy()
    recent["indicator"] = ind_values.tail(lookback).values

    # 检测底背离：价格创新低但指标未创新低
    price_lows = _find_local_minima(recent["close"].values, window=3)
    ind_lows = _find_local_minima(recent["indicator"].values, window=3)

    if len(price_lows) >= 2 and len(ind_lows) >= 2:
        pl1, pl2 = price_lows[-2], price_lows[-1]
        il1, il2 = ind_lows[-2], ind_lows[-1]

        # 价格新低但指标不新低
        if (
            recent["close"].iloc[pl2] < recent["close"].iloc[pl1]
            and recent["indicator"].iloc[il2] > recent["indicator"].iloc[il1]
        ):
            strength = _calc_divergence_strength(
                recent["close"].iloc[pl1],
                recent["close"].iloc[pl2],
                recent["indicator"].iloc[il1],
                recent["indicator"].iloc[il2],
            )
            results.append(
                {
                    "type": "bullish_divergence",
                    "type_cn": "底背离",
                    "indicator": indicator,
                    "date": str(recent.index[pl2]) if hasattr(recent.index[pl2], "strftime") else str(pl2),
                    "price_prev": round(float(recent["close"].iloc[pl1]), 2),
                    "price_curr": round(float(recent["close"].iloc[pl2]), 2),
                    "ind_prev": round(float(recent["indicator"].iloc[il1]), 2),
                    "ind_curr": round(float(recent["indicator"].iloc[il2]), 2),
                    "strength": strength,
                    "description": f"价格创新低 {recent['close'].iloc[pl2]:.2f} 但{indicator.upper()}未创新低，底背离信号",
                }
            )

    # 检测顶背离：价格创新高但指标未创新高
    price_highs = _find_local_maxima(recent["close"].values, window=3)
    ind_highs = _find_local_maxima(recent["indicator"].values, window=3)

    if len(price_highs) >= 2 and len(ind_highs) >= 2:
        ph1, ph2 = price_highs[-2], price_highs[-1]
        ih1, ih2 = ind_highs[-2], ind_highs[-1]

        # 价格新高但指标不新高
        if (
            recent["close"].iloc[ph2] > recent["close"].iloc[ph1]
            and recent["indicator"].iloc[ih2] < recent["indicator"].iloc[ih1]
        ):
            strength = _calc_divergence_strength(
                recent["close"].iloc[ph1],
                recent["close"].iloc[ph2],
                recent["indicator"].iloc[ih1],
                recent["indicator"].iloc[ih2],
            )
            results.append(
                {
                    "type": "bearish_divergence",
                    "type_cn": "顶背离",
                    "indicator": indicator,
                    "date": str(recent.index[ph2]) if hasattr(recent.index[ph2], "strftime") else str(ph2),
                    "price_prev": round(float(recent["close"].iloc[ph1]), 2),
                    "price_curr": round(float(recent["close"].iloc[ph2]), 2),
                    "ind_prev": round(float(recent["indicator"].iloc[ih1]), 2),
                    "ind_curr": round(float(recent["indicator"].iloc[ih2]), 2),
                    "strength": strength,
                    "description": f"价格创新高 {recent['close'].iloc[ph2]:.2f} 但{indicator.upper()}未创新高，顶背离信号",
                }
            )

    return results


def detect_all_divergences(df: pd.DataFrame, lookback: int = 20) -> list[dict]:
    """检测所有指标的背离"""
    results = []
    for ind in ["rsi", "macd", "kdj"]:
        results.extend(detect_divergence(df, ind, lookback))
    return results


def _find_local_minima(series: np.ndarray, window: int = 3) -> list[int]:
    """查找局部极小值点"""
    minima = []
    for i in range(window, len(series) - window):
        if all(series[i] <= series[i - j] for j in range(1, window + 1)) and all(
            series[i] <= series[i + j] for j in range(1, min(window + 1, len(series) - i))
        ):
            minima.append(i)
    return minima


def _find_local_maxima(series: np.ndarray, window: int = 3) -> list[int]:
    """查找局部极大值点"""
    maxima = []
    for i in range(window, len(series) - window):
        if all(series[i] >= series[i - j] for j in range(1, window + 1)) and all(
            series[i] >= series[i + j] for j in range(1, min(window + 1, len(series) - i))
        ):
            maxima.append(i)
    return maxima


def _calc_divergence_strength(price_prev, price_curr, ind_prev, ind_curr) -> str:
    """计算背离强度"""
    price_change = abs(price_curr - price_prev) / price_prev if price_prev else 0
    ind_change = abs(ind_curr - ind_prev) / abs(ind_prev) if ind_prev else 0

    # 价格变化大但指标变化小 = 强背离
    if price_change > 0.05 and ind_change < 0.1:
        return "strong"
    elif price_change > 0.03:
        return "medium"
    else:
        return "weak"
