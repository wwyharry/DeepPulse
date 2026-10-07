"""行情画像统计 - 从带指标的 K线 DataFrame 提取量化摘要

原 LLM 策略生成器（LLMStrategyGenerator/apply_actions）已随策略平台
降级移除；策略构思由 Agent 自行完成（见 prompts「操作逻辑量化校验」），
本模块只保留纯计算的行情画像函数。
"""

import numpy as np
import pandas as pd


def build_kline_stats(df: pd.DataFrame) -> dict:
    """从带指标的 K线 DataFrame 提取行情画像，供 prompt/引擎使用"""
    if df is None or df.empty:
        return {}
    close = df["close"]
    ret = close.pct_change().dropna()
    stats: dict = {
        "bars": int(len(df)),
        "latest_close": round(float(close.iloc[-1]), 2),
        "period_return_pct": round(float((close.iloc[-1] / close.iloc[0] - 1) * 100), 2),
        "annual_volatility_pct": round(float(ret.std() * np.sqrt(244) * 100), 2) if len(ret) > 1 else 0.0,
        "avg_volume": round(float(df["volume"].mean()), 0) if "volume" in df else 0.0,
    }
    # 区间最大回撤
    cummax = close.cummax()
    drawdown = (close - cummax) / cummax
    stats["max_drawdown_pct"] = round(float(drawdown.min() * 100), 2)
    # 最新指标快照
    last = df.iloc[-1]
    snapshot = {}
    for col in [
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "dif",
        "dea",
        "macd",
        "rsi6",
        "rsi14",
        "k",
        "d",
        "j",
        "boll_upper",
        "boll_mid",
        "boll_lower",
        "atr14",
        "vol_ratio",
    ]:
        if col in df.columns and pd.notna(last.get(col)):
            snapshot[col] = round(float(last[col]), 4)
    stats["indicator_snapshot"] = snapshot
    return stats
