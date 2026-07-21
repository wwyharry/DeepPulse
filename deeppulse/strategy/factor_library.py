"""因子库 - 量化因子定义，供 LLM 策略生成与网格搜索调优使用

每个因子输出 0~1 的逐 bar 得分 Series，因子参数自带网格搜索范围
（FactorParam 的 min/max/step），供自动优化循环做参数调优。
因子基于 TechnicalIndicators.add_all() 已计算的指标列实现。
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class FactorParam:
    """因子参数定义（含网格搜索范围）"""

    default: float
    min: float
    max: float
    step: float

    def grid(self) -> list[float]:
        """生成网格搜索候选值"""
        values = []
        v = self.min
        while v <= self.max + 1e-9:
            values.append(round(v, 6))
            v += self.step
        if self.default not in values:
            values.append(self.default)
            values.sort()
        return values


@dataclass
class Factor:
    """量化因子定义"""

    id: str
    name: str
    description: str
    params: dict[str, FactorParam] = field(default_factory=dict)
    compute: Callable[[pd.DataFrame, dict], pd.Series] | None = None

    def score(self, df: pd.DataFrame, params: dict | None = None) -> pd.Series:
        """计算因子得分（0~1），对缺列/NaN 安全容错"""
        merged = {k: p.default for k, p in self.params.items()}
        if params:
            merged.update({k: v for k, v in params.items() if k in self.params})
        try:
            result = self.compute(df, merged)
            if not isinstance(result, pd.Series):
                result = pd.Series(result, index=df.index)
            return result.reindex(df.index).fillna(0.0).clip(0.0, 1.0).astype(float)
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            return pd.Series(0.0, index=df.index)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """安全取列，缺失时返回全 NaN"""
    if name in df.columns:
        return df[name]
    return pd.Series(np.nan, index=df.index)


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


# ============ 因子实现 ============


def _ma_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """均线多头排列：短期均线在长期均线上方且向上发散"""
    fast, slow = int(p["fast"]), int(p["slow"])
    ma_fast = _col(df, "close").rolling(fast).mean()
    ma_slow = _col(df, "close").rolling(slow).mean()
    above = (ma_fast > ma_slow).astype(float)
    rising = (ma_slow > ma_slow.shift(3)).astype(float)
    strength = ((ma_fast - ma_slow) / ma_slow.replace(0, np.nan)).clip(0, 0.1) / 0.1
    return above * 0.5 + rising * 0.2 + strength.fillna(0) * 0.3


def _ma_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    """均线金叉：短期均线上穿长期均线（事件型，近期发生得分高）"""
    fast, slow = int(p["fast"]), int(p["slow"])
    ma_fast = _col(df, "close").rolling(fast).mean()
    ma_slow = _col(df, "close").rolling(slow).mean()
    crossed = _cross_up(ma_fast, ma_slow)
    # 金叉发生后 hold_days 天内得分衰减
    hold = int(p["hold_days"])
    score = pd.Series(0.0, index=df.index)
    last_cross = -10**9
    values = crossed.fillna(False).values
    out = np.zeros(len(df))
    for i in range(len(df)):
        if values[i]:
            last_cross = i
        gap = i - last_cross
        if gap <= hold:
            out[i] = 1.0 - gap / (hold + 1)
    score[:] = out
    return score


def _macd_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD 金叉：DIF 上穿 DEA，零轴下方金叉更强"""
    dif, dea = _col(df, "dif"), _col(df, "dea")
    crossed = _cross_up(dif, dea)
    hold = int(p["hold_days"])
    out = np.zeros(len(df))
    last_cross = -10**9
    for i, v in enumerate(crossed.fillna(False).values):
        if v:
            last_cross = i
        gap = i - last_cross
        if gap <= hold:
            base = 1.0 - gap / (hold + 1)
            below_zero = dif.iloc[i] < 0 if pd.notna(dif.iloc[i]) else False
            out[i] = min(1.0, base * (1.2 if below_zero else 0.9))
    return pd.Series(out, index=df.index)


def _macd_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD 趋势走强：DIF>DEA 且柱体放大"""
    dif, dea, hist = _col(df, "dif"), _col(df, "dea"), _col(df, "macd")
    bullish = (dif > dea).astype(float)
    expanding = (hist > hist.shift(1)).astype(float)
    return bullish * 0.6 + expanding * 0.4


def _rsi_oversold(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI 超卖反转：RSI 从超卖区回升"""
    rsi = _col(df, "rsi6")
    threshold = p["threshold"]
    was_oversold = rsi.shift(1) < threshold
    recovering = rsi >= rsi.shift(1)
    depth = ((threshold - rsi.shift(1)) / threshold).clip(0, 1)
    score = (was_oversold & recovering).astype(float) * (0.5 + 0.5 * depth)
    return score


def _rsi_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI 强势：RSI 处于强势区但未超买"""
    rsi = _col(df, "rsi14")
    low, high = p["low"], p["high"]
    in_zone = (rsi >= low) & (rsi <= high)
    position = ((rsi - low) / (high - low)).clip(0, 1)
    return in_zone.astype(float) * (0.5 + 0.5 * position)


def _kdj_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    """KDJ 金叉：K 上穿 D，低位金叉更强"""
    k, d = _col(df, "k"), _col(df, "d")
    crossed = _cross_up(k, d)
    low_zone = (k < p["low_level"]).astype(float)
    return crossed.astype(float) * (0.6 + 0.4 * low_zone)


def _boll_breakout(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林上轨突破：收盘价放量突破布林上轨"""
    close, upper = _col(df, "close"), _col(df, "boll_upper")
    vol_ratio = _col(df, "vol_ratio")
    breakout = close > upper
    vol_ok = vol_ratio >= p["vol_ratio"]
    excess = ((close - upper) / upper.replace(0, np.nan)).clip(0, 0.05) / 0.05
    return (breakout & vol_ok).astype(float) * (0.6 + 0.4 * excess.fillna(0))


def _boll_bounce(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林下轨反弹：触及下轨后收回"""
    low, close, lower = _col(df, "low"), _col(df, "close"), _col(df, "boll_lower")
    touched = low <= lower * (1 + p["tolerance"] / 100)
    recovered = close > lower
    return (touched & recovered).astype(float)


def _volume_breakout(df: pd.DataFrame, p: dict) -> pd.Series:
    """放量上涨：量比放大且涨幅达标"""
    vol_ratio, pct = _col(df, "vol_ratio"), _col(df, "pct_change")
    vol_ok = vol_ratio >= p["vol_ratio"]
    pct_ok = pct >= p["pct"]
    strength = ((vol_ratio - p["vol_ratio"]) / p["vol_ratio"]).clip(0, 1)
    return (vol_ok & pct_ok).astype(float) * (0.6 + 0.4 * strength.fillna(0))


def _obv_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """OBV 上行：量能持续积累"""
    obv = _col(df, "obv")
    period = int(p["period"])
    obv_ma = obv.rolling(period).mean()
    rising = (obv > obv_ma).astype(float)
    slope = (obv_ma > obv_ma.shift(3)).astype(float)
    return rising * 0.6 + slope * 0.4


def _momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """N 日动量：区间涨幅归一化"""
    period = int(p["period"])
    close = _col(df, "close")
    mom = close.pct_change(period) * 100
    return (mom / p["scale"]).clip(0, 1)


def _atr_volatility(df: pd.DataFrame, p: dict) -> pd.Series:
    """低波动优选：ATR 占价格比越低得分越高"""
    atr, close = _col(df, "atr14"), _col(df, "close")
    ratio = atr / close.replace(0, np.nan) * 100
    return (1 - (ratio / p["max_ratio"]).clip(0, 1)).fillna(0)


def _price_position(df: pd.DataFrame, p: dict) -> pd.Series:
    """区间低位：收盘价处于 N 日价格区间的相对低位"""
    period = int(p["period"])
    close = _col(df, "close")
    highest = _col(df, "high").rolling(period).max()
    lowest = _col(df, "low").rolling(period).min()
    span = (highest - lowest).replace(0, np.nan)
    position = (close - lowest) / span
    target = p["target"]
    # 越接近 target 位置得分越高
    return (1 - (position - target).abs() / max(target, 1 - target)).clip(0, 1).fillna(0)


def _high_52w(df: pd.DataFrame, p: dict) -> pd.Series:
    """逼近区间新高：距离 N 日最高点越近得分越高"""
    period = int(p["period"])
    close = _col(df, "close")
    highest = _col(df, "high").rolling(period).max()
    dist = (highest - close) / highest.replace(0, np.nan) * 100
    return (1 - (dist / p["max_dist"]).clip(0, 1)).fillna(0)


# ============ 因子注册表 ============

_FACTORS: dict[str, Factor] = {}


def _register(factor: Factor) -> None:
    _FACTORS[factor.id] = factor


_register(Factor(
    id="ma_trend", name="均线多头排列",
    description="短期均线在长期均线上方且向上发散，适合趋势跟随",
    params={
        "fast": FactorParam(default=5, min=3, max=20, step=1),
        "slow": FactorParam(default=20, min=10, max=60, step=5),
    },
    compute=_ma_trend,
))
_register(Factor(
    id="ma_cross", name="均线金叉",
    description="短期均线上穿长期均线，事件型信号，金叉后数日内有效",
    params={
        "fast": FactorParam(default=5, min=3, max=15, step=1),
        "slow": FactorParam(default=10, min=10, max=60, step=5),
        "hold_days": FactorParam(default=5, min=1, max=15, step=1),
    },
    compute=_ma_cross,
))
_register(Factor(
    id="macd_cross", name="MACD金叉",
    description="DIF 上穿 DEA，零轴下方金叉反转意味更强",
    params={"hold_days": FactorParam(default=5, min=1, max=15, step=1)},
    compute=_macd_cross,
))
_register(Factor(
    id="macd_trend", name="MACD趋势走强",
    description="DIF 在 DEA 上方且 MACD 柱体持续放大",
    params={},
    compute=_macd_trend,
))
_register(Factor(
    id="rsi_oversold", name="RSI超卖反转",
    description="RSI6 从超卖区回升，适合抄底反弹",
    params={"threshold": FactorParam(default=30, min=15, max=40, step=5)},
    compute=_rsi_oversold,
))
_register(Factor(
    id="rsi_momentum", name="RSI强势区",
    description="RSI14 处于强势区但未超买，趋势延续",
    params={
        "low": FactorParam(default=50, min=40, max=60, step=5),
        "high": FactorParam(default=70, min=60, max=85, step=5),
    },
    compute=_rsi_momentum,
))
_register(Factor(
    id="kdj_cross", name="KDJ金叉",
    description="K 线上穿 D 线，低位金叉更可靠",
    params={"low_level": FactorParam(default=50, min=20, max=60, step=10)},
    compute=_kdj_cross,
))
_register(Factor(
    id="boll_breakout", name="布林上轨突破",
    description="放量突破布林上轨，强势突破信号",
    params={"vol_ratio": FactorParam(default=1.5, min=1.0, max=3.0, step=0.5)},
    compute=_boll_breakout,
))
_register(Factor(
    id="boll_bounce", name="布林下轨反弹",
    description="触及布林下轨后收回，超跌反弹信号",
    params={"tolerance": FactorParam(default=1.0, min=0.0, max=3.0, step=0.5)},
    compute=_boll_bounce,
))
_register(Factor(
    id="volume_breakout", name="放量上涨",
    description="量比显著放大且涨幅达标，资金进场信号",
    params={
        "vol_ratio": FactorParam(default=2.0, min=1.2, max=4.0, step=0.4),
        "pct": FactorParam(default=3.0, min=1.0, max=6.0, step=0.5),
    },
    compute=_volume_breakout,
))
_register(Factor(
    id="obv_trend", name="OBV量能上行",
    description="OBV 在其均线上方且均线上行，量能持续积累",
    params={"period": FactorParam(default=20, min=10, max=40, step=5)},
    compute=_obv_trend,
))
_register(Factor(
    id="momentum", name="N日动量",
    description="N 日涨幅归一化得分，强者恒强",
    params={
        "period": FactorParam(default=20, min=5, max=60, step=5),
        "scale": FactorParam(default=20.0, min=10.0, max=40.0, step=5.0),
    },
    compute=_momentum,
))
_register(Factor(
    id="atr_volatility", name="低波动优选",
    description="ATR 占价格比越低得分越高，回避剧烈波动",
    params={"max_ratio": FactorParam(default=5.0, min=2.0, max=8.0, step=1.0)},
    compute=_atr_volatility,
))
_register(Factor(
    id="price_position", name="区间低位",
    description="收盘价处于 N 日价格区间相对低位，低吸型因子",
    params={
        "period": FactorParam(default=60, min=20, max=120, step=10),
        "target": FactorParam(default=0.3, min=0.1, max=0.5, step=0.1),
    },
    compute=_price_position,
))
_register(Factor(
    id="high_52w", name="逼近区间新高",
    description="距离 N 日最高点越近得分越高，强势股特征",
    params={
        "period": FactorParam(default=250, min=60, max=250, step=10),
        "max_dist": FactorParam(default=10.0, min=3.0, max=20.0, step=1.0),
    },
    compute=_high_52w,
))


def list_factors() -> list[Factor]:
    """返回全部因子列表"""
    return list(_FACTORS.values())


def get_factor(factor_id: str) -> Factor:
    """按 id 获取因子，未知 id 抛 KeyError"""
    if factor_id not in _FACTORS:
        raise KeyError(f"未知因子: {factor_id}，可用因子: {sorted(_FACTORS)}")
    return _FACTORS[factor_id]
