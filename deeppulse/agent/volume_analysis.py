"""深度量价分析模块

分析成交量与价格的关系，识别异常量能和量价背离。
"""

import numpy as np
import pandas as pd


def analyze_volume_price(df: pd.DataFrame) -> dict:
    """深度量价分析

    Args:
        df: 包含 OHLCV 的 DataFrame

    Returns:
        {
            "vol_trend": "increasing" / "decreasing" / "stable",
            "vol_price_sync": True/False,
            "recent_pattern": str,
            "anomalies": [...],
            "conclusion": str
        }
    """
    if len(df) < 20:
        return {"conclusion": "数据不足"}

    close = df["close"]
    volume = df["volume"]
    high = df["high"]
    low = df["low"]

    # 量能趋势
    vol_trend = _analyze_volume_trend(volume)

    # 量价同步性
    vol_price_sync = _check_volume_price_sync(close, volume)

    # 近期量价模式
    recent_pattern = _identify_recent_pattern(close, volume, high, low)

    # 异常量能
    anomalies = _detect_volume_anomalies(close, volume)

    # 量价背离
    divergences = _detect_volume_divergence(close, volume)

    # 生成结论
    conclusion = _generate_conclusion(vol_trend, vol_price_sync, recent_pattern, anomalies, divergences)

    return {
        "vol_trend": vol_trend["direction"],
        "vol_trend_cn": vol_trend["direction_cn"],
        "vol_ratio_5d": round(vol_trend["ratio_5d"], 2),
        "vol_ratio_10d": round(vol_trend["ratio_10d"], 2),
        "vol_price_sync": vol_price_sync,
        "recent_pattern": recent_pattern,
        "anomalies": anomalies,
        "divergences": divergences,
        "conclusion": conclusion,
    }


def _analyze_volume_trend(volume: pd.Series) -> dict:
    """分析量能趋势"""
    vol_5 = volume.tail(5).mean()
    vol_10 = volume.tail(10).mean()
    vol_20 = volume.tail(20).mean()

    ratio_5d = vol_5 / vol_20 if vol_20 > 0 else 1
    ratio_10d = vol_10 / vol_20 if vol_20 > 0 else 1

    if ratio_5d > 1.5:
        direction = "increasing"
        direction_cn = "明显放量"
    elif ratio_5d > 1.2:
        direction = "increasing"
        direction_cn = "温和放量"
    elif ratio_5d < 0.5:
        direction = "decreasing"
        direction_cn = "明显缩量"
    elif ratio_5d < 0.8:
        direction = "decreasing"
        direction_cn = "温和缩量"
    else:
        direction = "stable"
        direction_cn = "量能平稳"

    return {
        "direction": direction,
        "direction_cn": direction_cn,
        "ratio_5d": ratio_5d,
        "ratio_10d": ratio_10d,
    }


def _check_volume_price_sync(close: pd.Series, volume: pd.Series) -> bool:
    """检查量价同步性"""
    # 最近5天的价格和成交量变化方向
    price_changes = close.tail(5).diff().dropna()
    vol_changes = volume.tail(5).diff().dropna()

    if len(price_changes) < 3:
        return True

    # 同向比例
    sync_count = sum(1 for p, v in zip(price_changes, vol_changes) if (p > 0 and v > 0) or (p < 0 and v < 0))
    return sync_count >= 2


def _identify_recent_pattern(close: pd.Series, volume: pd.Series, high: pd.Series, low: pd.Series) -> str:
    """识别近期量价模式"""
    if len(close) < 5:
        return "数据不足"

    last_close = close.iloc[-1]
    prev_close = close.iloc[-2]
    last_vol = volume.iloc[-1]
    avg_vol = volume.tail(5).mean()

    pct = (last_close / prev_close - 1) * 100
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1

    if pct > 3 and vol_ratio > 2:
        return "放量大涨"
    elif pct > 1 and vol_ratio > 1.5:
        return "放量上涨"
    elif pct > 0 and vol_ratio < 0.7:
        return "缩量上涨"
    elif pct < -3 and vol_ratio > 2:
        return "放量大跌"
    elif pct < -1 and vol_ratio > 1.5:
        return "放量下跌"
    elif pct < 0 and vol_ratio < 0.7:
        return "缩量下跌"
    elif vol_ratio > 3:
        return "异常放量"
    elif vol_ratio < 0.3:
        return "极度缩量"
    else:
        return "正常量价"


def _detect_volume_anomalies(close: pd.Series, volume: pd.Series) -> list[dict]:
    """检测异常量能"""
    anomalies = []
    avg_vol = volume.tail(20).mean()
    std_vol = volume.tail(20).std()

    if avg_vol == 0:
        return anomalies

    for i in range(-5, 0):
        if abs(i) > len(volume):
            continue
        vol = volume.iloc[i]
        ratio = vol / avg_vol

        if ratio > 3:
            anomalies.append({
                "type": "天量",
                "vol_ratio": round(ratio, 1),
                "significance": "高度关注",
                "description": f"成交量为20日均量的 {ratio:.1f} 倍",
            })
        elif ratio > 2:
            anomalies.append({
                "type": "放量",
                "vol_ratio": round(ratio, 1),
                "significance": "注意",
                "description": f"成交量为20日均量的 {ratio:.1f} 倍",
            })

    return anomalies


def _detect_volume_divergence(close: pd.Series, volume: pd.Series) -> list[dict]:
    """检测量价背离"""
    divergences = []

    if len(close) < 10:
        return divergences

    # 最近5天价格趋势
    price_trend = close.iloc[-1] - close.iloc[-6]
    vol_trend = volume.tail(5).mean() - volume.tail(10).head(5).mean()

    # 量价背离
    if price_trend > 0 and vol_trend < 0:
        divergences.append({
            "type": "量价背离",
            "direction": "顶背离",
            "description": "价格上涨但成交量萎缩，上涨动能不足",
        })
    elif price_trend < 0 and vol_trend > 0:
        divergences.append({
            "type": "量价背离",
            "direction": "底背离",
            "description": "价格下跌但成交量放大，可能有资金介入",
        })

    return divergences


def _generate_conclusion(vol_trend, vol_price_sync, recent_pattern, anomalies, divergences) -> str:
    """生成量价分析结论"""
    parts = []

    parts.append(f"量能趋势：{vol_trend['direction_cn']}（5日/20日量比 {vol_trend['ratio_5d']:.1f}）。")

    if vol_price_sync:
        parts.append("量价同步，趋势健康。")
    else:
        parts.append("量价不同步，注意趋势持续性。")

    parts.append(f"近期模式：{recent_pattern}。")

    if anomalies:
        types = [a["type"] for a in anomalies]
        parts.append(f"异常信号：{'、'.join(set(types))}。")

    if divergences:
        for d in divergences:
            parts.append(f"⚠️ {d['description']}")

    return "".join(parts)
