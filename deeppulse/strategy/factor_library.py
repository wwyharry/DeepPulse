"""因子库 - 量化因子定义，供 LLM 策略生成与网格搜索调优使用

每个因子输出 0~1 的逐 bar 得分 Series，因子参数自带网格搜索范围
（FactorParam 的 min/max/step），供自动优化循环做参数调优。
因子基于 TechnicalIndicators.add_all() 已计算的指标列实现。

因子分类：
1.  均线类 (MA)        — 趋势方向与交叉
2.  MACD 类            — 趋势动量与背离
3.  RSI 类             — 超买超卖与动量
4.  KDJ 类             — 随机指标
5.  布林带类 (BOLL)    — 波动与突破
6.  量能类 (VOL)       — 成交量与资金流
7.  动量类 (MOM)       — 价格动量与加速度
8.  波动类 (VOLAT)     — 波动率与稳定性
9.  趋势强度类 (TREND) — ADX 等趋势确认
10. 价格位置类 (POS)   — 相对位置与支撑阻力
11. 形态类 (PATTERN)   — K线形态识别
12. 统计类 (STAT)      — 统计特征
13. 复合类 (COMP)      — 多因子组合
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
    category: str = ""  # 所属分类（注册时由 _set_category 打标）

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


# ============ 工具函数 ============


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """安全取列，缺失时返回全 NaN"""
    if name in df.columns:
        return df[name]
    return pd.Series(np.nan, index=df.index)


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    """a 上穿 b"""
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    """a 下穿 b"""
    return (a < b) & (a.shift(1) >= b.shift(1))


def _rolling_rank(s: pd.Series, period: int) -> pd.Series:
    """滚动百分位排名 0~1（真实排名，ties 取平均秩；全 ties 窗口居中得 0.5）"""
    return s.rolling(period).apply(
        lambda x: (x.rank(method="average").iloc[-1] - 1) / (len(x) - 1) if len(x) > 1 else 0.5,
        raw=False,
    )


def _ema(s: pd.Series, span: int) -> pd.Series:
    """指数移动平均"""
    return s.ewm(span=span, adjust=False).mean()


def _wma(s: pd.Series, period: int) -> pd.Series:
    """加权移动平均"""
    weights = np.arange(1, period + 1, dtype=float)
    return s.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


# ============ 1. 均线类因子 ============


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
    """均线金叉：短期均线上穿长期均线"""
    fast, slow = int(p["fast"]), int(p["slow"])
    ma_fast = _col(df, "close").rolling(fast).mean()
    ma_slow = _col(df, "close").rolling(slow).mean()
    crossed = _cross_up(ma_fast, ma_slow)
    hold = int(p["hold_days"])
    out = np.zeros(len(df))
    last_cross = -(10**9)
    for i, v in enumerate(crossed.fillna(False).values):
        if v:
            last_cross = i
        gap = i - last_cross
        if gap <= hold:
            out[i] = 1.0 - gap / (hold + 1)
    return pd.Series(out, index=df.index)


def _ma_support(df: pd.DataFrame, p: dict) -> pd.Series:
    """均线支撑：价格回踩均线不破"""
    period = int(p["period"])
    close = _col(df, "close")
    low = _col(df, "low")
    ma = close.rolling(period).mean()
    tolerance = p["tolerance"] / 100
    near_ma = (low <= ma * (1 + tolerance)) & (close >= ma * (1 - tolerance))
    above_ma = close >= ma
    return (near_ma & above_ma).astype(float)


def _ma_slope(df: pd.DataFrame, p: dict) -> pd.Series:
    """均线斜率：均线持续上升"""
    period = int(p["period"])
    close = _col(df, "close")
    ma = close.rolling(period).mean()
    slope_period = int(p["slope_period"])
    slope = (ma - ma.shift(slope_period)) / ma.shift(slope_period).replace(0, np.nan) * 100
    return (slope / p["max_slope"]).clip(0, 1).fillna(0)


def _ma_compression(df: pd.DataFrame, p: dict) -> pd.Series:
    """均线收敛：多条均线收敛后即将发散"""
    close = _col(df, "close")
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    spread = (ma5 - ma20).abs() / ma20.replace(0, np.nan) * 100
    compressed = (spread < p["threshold"]).astype(float)
    turning = (spread > spread.shift(1)).astype(float)
    return compressed * 0.6 + turning * 0.4


def _ema_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """EMA趋势：价格在EMA上方"""
    period = int(p["period"])
    close = _col(df, "close")
    ema = _ema(close, period)
    above = (close > ema).astype(float)
    dist = ((close - ema) / ema.replace(0, np.nan) * 100).clip(0, 5) / 5
    return above * 0.6 + dist.fillna(0) * 0.4


def _price_ma_distance(df: pd.DataFrame, p: dict) -> pd.Series:
    """价格偏离均线：价格偏离均线的合理范围"""
    period = int(p["period"])
    close = _col(df, "close")
    ma = close.rolling(period).mean()
    dist = (close - ma) / ma.replace(0, np.nan) * 100
    # 偏离适中得分高（不追高不抄底过深）
    ideal = p["ideal_dist"]
    score = (1 - (dist - ideal).abs() / p["max_dev"]).clip(0, 1)
    return score.fillna(0)


def _double_ma_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    """双均线交叉确认：快慢均线同时向上"""
    fast, slow = int(p["fast"]), int(p["slow"])
    close = _col(df, "close")
    ma_fast = close.rolling(fast).mean()
    ma_slow = close.rolling(slow).mean()
    both_rising = (ma_fast > ma_fast.shift(1)) & (ma_slow > ma_slow.shift(1))
    fast_above = ma_fast > ma_slow
    return (both_rising & fast_above).astype(float)


def _triple_ma(df: pd.DataFrame, p: dict) -> pd.Series:
    """三均线确认：短>中>长且全部上升"""
    close = _col(df, "close")
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    aligned = (ma5 > ma10) & (ma10 > ma20)
    all_rising = (ma5 > ma5.shift(2)) & (ma10 > ma10.shift(2)) & (ma20 > ma20.shift(2))
    return (aligned & all_rising).astype(float)


# ============ 2. MACD 类因子 ============


def _macd_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD金叉：DIF上穿DEA"""
    dif, dea = _col(df, "dif"), _col(df, "dea")
    crossed = _cross_up(dif, dea)
    hold = int(p["hold_days"])
    out = np.zeros(len(df))
    last_cross = -(10**9)
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
    """MACD趋势走强：DIF>DEA且柱体放大"""
    dif, dea, hist = _col(df, "dif"), _col(df, "dea"), _col(df, "macd")
    bullish = (dif > dea).astype(float)
    expanding = (hist > hist.shift(1)).astype(float)
    return bullish * 0.6 + expanding * 0.4


def _macd_histogram(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD柱体：柱体由负转正或持续放大"""
    hist = _col(df, "macd")
    turning_positive = _cross_up(hist, pd.Series(0, index=df.index))
    expanding = hist > hist.shift(1)
    positive = hist > 0
    score = turning_positive.astype(float) * 0.5 + (positive & expanding).astype(float) * 0.5
    return score


def _macd_zero_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD零轴穿越：DIF上穿零轴"""
    dif = _col(df, "dif")
    crossed = _cross_up(dif, pd.Series(0, index=df.index))
    hold = int(p["hold_days"])
    out = np.zeros(len(df))
    last_cross = -(10**9)
    for i, v in enumerate(crossed.fillna(False).values):
        if v:
            last_cross = i
        gap = i - last_cross
        if gap <= hold:
            out[i] = 1.0 - gap / (hold + 1)
    return pd.Series(out, index=df.index)


def _macd_divergence(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD底背离：价格新低但MACD不创新低"""
    close = _col(df, "close")
    dif = _col(df, "dif")
    period = int(p["period"])
    tol = p["tolerance"] / 100
    price_low = close.rolling(period).min()
    dif_low = dif.rolling(period).min()
    price_new_low = close <= price_low * (1 + tol)
    # dif_low 为负时乘法方向反转，须按符号选 (1±tol)：阈值语义始终是「比窗口低点高 tol%」
    dif_thr = (dif_low * (1 + tol)).where(dif_low > 0, dif_low * (1 - tol))
    dif_higher = dif > dif_thr
    return (price_new_low & dif_higher).astype(float)


def _macd_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD动量：DIF和DEA同向且差距扩大"""
    dif, dea = _col(df, "dif"), _col(df, "dea")
    same_dir = (dif > 0) & (dea > 0)
    gap = (dif - dea).abs()
    gap_expanding = gap > gap.shift(1)
    return (same_dir & gap_expanding).astype(float)


# ============ 3. RSI 类因子 ============


def _rsi_oversold(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI超卖反转：RSI从超卖区回升"""
    rsi = _col(df, "rsi6")
    threshold = p["threshold"]
    was_oversold = rsi.shift(1) < threshold
    recovering = rsi >= rsi.shift(1)
    depth = ((threshold - rsi.shift(1)) / threshold).clip(0, 1)
    score = (was_oversold & recovering).astype(float) * (0.5 + 0.5 * depth)
    return score


def _rsi_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI强势区：RSI处于强势区但未超买"""
    rsi = _col(df, "rsi14")
    low, high = p["low"], p["high"]
    in_zone = (rsi >= low) & (rsi <= high)
    position = ((rsi - low) / (high - low)).clip(0, 1)
    return in_zone.astype(float) * (0.5 + 0.5 * position)


def _rsi_bullish(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI看涨：RSI从低位上升且突破50"""
    rsi = _col(df, "rsi14")
    crossing_50 = _cross_up(rsi, pd.Series(50, index=df.index))
    rising = rsi > rsi.shift(1)
    above_50 = rsi > 50
    return crossing_50.astype(float) * 0.5 + (rising & above_50).astype(float) * 0.5


def _rsi_divergence(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI底背离：价格新低但RSI不创新低"""
    close = _col(df, "close")
    rsi = _col(df, "rsi14")
    period = int(p["period"])
    price_low = close.rolling(period).min()
    rsi_low = rsi.rolling(period).min()
    price_new_low = close <= price_low * (1 + 0.02)
    rsi_higher = rsi > rsi_low + 3
    return (price_new_low & rsi_higher).astype(float)


def _rsi_range(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI适中区间：RSI在中性偏强区间"""
    rsi = _col(df, "rsi14")
    low, high = p["low"], p["high"]
    in_range = (rsi >= low) & (rsi <= high)
    return in_range.astype(float)


def _rsi_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI趋势上升：RSI持续走高"""
    rsi = _col(df, "rsi14")
    period = int(p["period"])
    rsi_ma = rsi.rolling(period).mean()
    above_ma = rsi > rsi_ma
    rising = rsi_ma > rsi_ma.shift(2)
    return (above_ma & rising).astype(float)


def _stoch_rsi(df: pd.DataFrame, p: dict) -> pd.Series:
    """随机RSI：RSI的随机指标，更敏感"""
    rsi = _col(df, "rsi14")
    period = int(p["period"])
    rsi_low = rsi.rolling(period).min()
    rsi_high = rsi.rolling(period).max()
    stoch = ((rsi - rsi_low) / (rsi_high - rsi_low + 1e-10)).clip(0, 1)
    oversold = stoch < p["threshold"]
    rising = stoch > stoch.shift(1)
    return (oversold & rising).astype(float) * 0.7 + stoch * 0.3


# ============ 4. KDJ 类因子 ============


def _kdj_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    """KDJ金叉：K上穿D"""
    k, d = _col(df, "k"), _col(df, "d")
    crossed = _cross_up(k, d)
    low_zone = (k < p["low_level"]).astype(float)
    return crossed.astype(float) * (0.6 + 0.4 * low_zone)


def _kdj_oversold(df: pd.DataFrame, p: dict) -> pd.Series:
    """KDJ超卖：K和D都在低位"""
    k, d = _col(df, "k"), _col(df, "d")
    threshold = p["threshold"]
    both_low = (k < threshold) & (d < threshold)
    turning = k > k.shift(1)
    return (both_low & turning).astype(float)


def _kdj_golden(df: pd.DataFrame, p: dict) -> pd.Series:
    """KDJ低位金叉：J从极低位回升"""
    j = _col(df, "j")
    threshold = p["threshold"]
    was_low = j.shift(1) < threshold
    rising = j > j.shift(1)
    return (was_low & rising).astype(float)


def _kdj_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """KDJ动量：J值持续上升"""
    j = _col(df, "j")
    rising_count = ((j > j.shift(1)).rolling(3).sum() >= 2).astype(float)
    in_range = ((j > 20) & (j < 80)).astype(float)
    return rising_count * 0.6 + in_range * 0.4


def _kdj_bullish(df: pd.DataFrame, p: dict) -> pd.Series:
    """KDJ看涨：K>D且J>D且全部上升"""
    k, d, j = _col(df, "k"), _col(df, "d"), _col(df, "j")
    bullish = (k > d) & (j > d)
    all_rising = (k > k.shift(1)) & (d > d.shift(1))
    return (bullish & all_rising).astype(float)


# ============ 5. 布林带类因子 ============


def _boll_breakout(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林上轨突破：放量突破布林上轨"""
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


def _boll_squeeze(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林收窄：带宽收窄后即将突破"""
    upper = _col(df, "boll_upper")
    lower = _col(df, "boll_lower")
    mid = _col(df, "boll_mid")
    bandwidth = (upper - lower) / mid.replace(0, np.nan) * 100
    squeezed = bandwidth < p["threshold"]
    close = _col(df, "close")
    breaking_up = close > mid
    return (squeezed & breaking_up).astype(float)


def _boll_position(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林位置：价格在布林带中的相对位置"""
    close, upper, lower = _col(df, "close"), _col(df, "boll_upper"), _col(df, "boll_lower")
    span = (upper - lower).replace(0, np.nan)
    position = ((close - lower) / span).clip(0, 1)
    # 适中位置得分高
    ideal = p["ideal"]
    return (1 - (position - ideal).abs() / max(ideal, 1 - ideal)).clip(0, 1).fillna(0)


def _boll_mid_support(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林中轨支撑：价格在中轨上方且中轨上升"""
    close, mid = _col(df, "close"), _col(df, "boll_mid")
    above_mid = close > mid
    mid_rising = mid > mid.shift(1)
    return (above_mid & mid_rising).astype(float)


# ============ 6. 量能类因子 ============


def _volume_breakout(df: pd.DataFrame, p: dict) -> pd.Series:
    """放量上涨：量比放大且涨幅达标"""
    vol_ratio, pct = _col(df, "vol_ratio"), _col(df, "pct_change")
    vol_ok = vol_ratio >= p["vol_ratio"]
    pct_ok = pct >= p["pct"]
    strength = ((vol_ratio - p["vol_ratio"]) / p["vol_ratio"]).clip(0, 1)
    return (vol_ok & pct_ok).astype(float) * (0.6 + 0.4 * strength.fillna(0))


def _obv_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """OBV上行：量能持续积累"""
    obv = _col(df, "obv")
    period = int(p["period"])
    obv_ma = obv.rolling(period).mean()
    rising = (obv > obv_ma).astype(float)
    slope = (obv_ma > obv_ma.shift(3)).astype(float)
    return rising * 0.6 + slope * 0.4


def _volume_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """量能趋势：成交量持续放大"""
    volume = _col(df, "volume")
    period = int(p["period"])
    vol_ma = volume.rolling(period).mean()
    above_ma = volume > vol_ma
    ma_rising = vol_ma > vol_ma.shift(2)
    return (above_ma & ma_rising).astype(float)


def _volume_price_confirm(df: pd.DataFrame, p: dict) -> pd.Series:
    """量价配合：价涨量增、价跌量缩"""
    close = _col(df, "close")
    volume = _col(df, "volume")
    price_up = close > close.shift(1)
    vol_up = volume > volume.shift(1)
    price_down = close < close.shift(1)
    vol_down = volume < volume.shift(1)
    good = (price_up & vol_up) | (price_down & vol_down)
    return good.astype(float)


def _volume_shrink(df: pd.DataFrame, p: dict) -> pd.Series:
    """缩量回调：价格回调但成交量萎缩"""
    close = _col(df, "close")
    volume = _col(df, "volume")
    period = int(p["period"])
    vol_ma = volume.rolling(period).mean()
    price_down = close < close.shift(3)
    vol_shrink = volume < vol_ma * p["ratio"]
    return (price_down & vol_shrink).astype(float)


def _mfi_signal(df: pd.DataFrame, p: dict) -> pd.Series:
    """MFI资金流：MFI从超卖区回升"""
    mfi = _col(df, "mfi")
    threshold = p["threshold"]
    was_low = mfi.shift(1) < threshold
    rising = mfi > mfi.shift(1)
    return (was_low & rising).astype(float)


def _mfi_divergence(df: pd.DataFrame, p: dict) -> pd.Series:
    """MFI背离：价格下跌但MFI上升"""
    close = _col(df, "close")
    mfi = _col(df, "mfi")
    period = int(p["period"])
    price_falling = close < close.shift(period)
    mfi_rising = mfi > mfi.shift(period)
    return (price_falling & mfi_rising).astype(float)


def _vwap_support(df: pd.DataFrame, p: dict) -> pd.Series:
    """VWAP支撑：价格在VWAP上方"""
    close = _col(df, "close")
    vwap = _col(df, "vwap")
    above = close > vwap
    dist = ((close - vwap) / vwap.replace(0, np.nan) * 100).clip(0, 5) / 5
    return above.astype(float) * 0.6 + dist.fillna(0) * 0.4


def _vwap_bounce(df: pd.DataFrame, p: dict) -> pd.Series:
    """VWAP回踩：价格回踩VWAP后反弹"""
    close, low = _col(df, "close"), _col(df, "low")
    vwap = _col(df, "vwap")
    tolerance = p["tolerance"] / 100
    touched = low <= vwap * (1 + tolerance)
    above = close >= vwap
    return (touched & above).astype(float)


def _volume_spike(df: pd.DataFrame, p: dict) -> pd.Series:
    """成交量异动：成交量突然放大"""
    volume = _col(df, "volume")
    period = int(p["period"])
    vol_ma = volume.rolling(period).mean()
    spike = volume > vol_ma * p["ratio"]
    close = _col(df, "close")
    up = close > close.shift(1)
    return (spike & up).astype(float)


def _accumulation(df: pd.DataFrame, p: dict) -> pd.Series:
    """吸筹信号：价格低位但量能持续"""
    close = _col(df, "close")
    volume = _col(df, "volume")
    period = int(p["period"])
    low_pos = (close < close.rolling(period).mean()).astype(float)
    vol_active = (volume > volume.rolling(period).mean() * 0.8).astype(float)
    return low_pos * 0.5 + vol_active * 0.5


# ============ 7. 动量类因子 ============


def _momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """N日动量：区间涨幅归一化"""
    period = int(p["period"])
    close = _col(df, "close")
    mom = close.pct_change(period) * 100
    return (mom / p["scale"]).clip(0, 1)


def _roc(df: pd.DataFrame, p: dict) -> pd.Series:
    """变化率ROC：价格变化率"""
    period = int(p["period"])
    close = _col(df, "close")
    roc = (close - close.shift(period)) / close.shift(period).replace(0, np.nan) * 100
    return (roc / p["scale"]).clip(0, 1).fillna(0)


def _momentum_acceleration(df: pd.DataFrame, p: dict) -> pd.Series:
    """动量加速度：动量在加速"""
    period = int(p["period"])
    close = _col(df, "close")
    mom = close.pct_change(period)
    accel = mom - mom.shift(period)
    return (accel > 0).astype(float) * 0.6 + (accel.clip(0, 0.1) / 0.1).fillna(0) * 0.4


def _rate_of_change(df: pd.DataFrame, p: dict) -> pd.Series:
    """多周期ROC：短期动量强于长期"""
    short = int(p["short"])
    long = int(p["long"])
    close = _col(df, "close")
    roc_short = close.pct_change(short)
    roc_long = close.pct_change(long)
    return (roc_short > roc_long).astype(float)


def _momentum_reversal(df: pd.DataFrame, p: dict) -> pd.Series:
    """动量反转：前期超跌后动量转正"""
    period = int(p["period"])
    close = _col(df, "close")
    prev_return = close.pct_change(period).shift(1)
    curr_return = close.pct_change(period)
    was_down = prev_return < -p["threshold"] / 100
    now_up = curr_return > 0
    return (was_down & now_up).astype(float)


def _williams_r(df: pd.DataFrame, p: dict) -> pd.Series:
    """威廉指标：%R从超卖区回升"""
    wr = _col(df, "williams_r")
    threshold = p["threshold"]  # -80
    was_oversold = wr.shift(1) < threshold
    rising = wr > wr.shift(1)
    return (was_oversold & rising).astype(float)


def _cci_signal(df: pd.DataFrame, p: dict) -> pd.Series:
    """CCI信号：CCI从超卖区回升"""
    cci = _col(df, "cci")
    threshold = p["threshold"]  # -100
    was_low = cci.shift(1) < threshold
    rising = cci > cci.shift(1)
    return (was_low & rising).astype(float)


def _cci_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """CCI趋势：CCI在强势区间"""
    cci = _col(df, "cci")
    low, high = p["low"], p["high"]
    in_range = (cci >= low) & (cci <= high)
    return in_range.astype(float)


def _momentum_consistency(df: pd.DataFrame, p: dict) -> pd.Series:
    """动量一致性：连续多日上涨"""
    close = _col(df, "close")
    period = int(p["period"])
    up_days = (close > close.shift(1)).rolling(period).sum()
    return (up_days / period).clip(0, 1)


# ============ 8. 波动类因子 ============


def _atr_volatility(df: pd.DataFrame, p: dict) -> pd.Series:
    """低波动优选：ATR占价格比越低得分越高"""
    atr, close = _col(df, "atr14"), _col(df, "close")
    ratio = atr / close.replace(0, np.nan) * 100
    return (1 - (ratio / p["max_ratio"]).clip(0, 1)).fillna(0)


def _atr_expansion(df: pd.DataFrame, p: dict) -> pd.Series:
    """ATR扩张：波动率开始放大（突破前兆）"""
    atr = _col(df, "atr14")
    atr_ma = atr.rolling(int(p["period"])).mean()
    expanding = atr > atr_ma * p["ratio"]
    close = _col(df, "close")
    up = close > close.shift(1)
    return (expanding & up).astype(float)


def _volatility_contraction(df: pd.DataFrame, p: dict) -> pd.Series:
    """波动收缩：波动率持续收缩后即将爆发"""
    atr = _col(df, "atr14")
    period = int(p["period"])
    atr_ma = atr.rolling(period).mean()
    contracted = atr < atr_ma * p["ratio"]
    turning = atr > atr.shift(1)
    return contracted.astype(float) * 0.6 + turning.astype(float) * 0.4


def _realized_vol(df: pd.DataFrame, p: dict) -> pd.Series:
    """实际波动率：近期波动率适中"""
    close = _col(df, "close")
    period = int(p["period"])
    returns = close.pct_change()
    vol = returns.rolling(period).std() * np.sqrt(252) * 100
    ideal = p["ideal"]
    return (1 - (vol - ideal).abs() / p["max_dev"]).clip(0, 1).fillna(0)


def _volatility_regime(df: pd.DataFrame, p: dict) -> pd.Series:
    """波动率状态：低波动环境"""
    close = _col(df, "close")
    returns = close.pct_change()
    short_vol = returns.rolling(int(p["short"])).std()
    long_vol = returns.rolling(int(p["long"])).std()
    low_regime = short_vol < long_vol
    return low_regime.astype(float)


def _boll_width(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林带宽：带宽收窄"""
    upper, lower, mid = _col(df, "boll_upper"), _col(df, "boll_lower"), _col(df, "boll_mid")
    width = (upper - lower) / mid.replace(0, np.nan) * 100
    period = int(p["period"])
    width_ma = width.rolling(period).mean()
    narrowing = width < width_ma
    return narrowing.astype(float)


def _keltner_position(df: pd.DataFrame, p: dict) -> pd.Series:
    """Keltner通道位置：价格在通道中轨上方"""
    close = _col(df, "close")
    atr = _col(df, "atr14")
    period = int(p["period"])
    ema = _ema(close, period)
    upper = ema + atr * p["mult"]
    lower = ema - atr * p["mult"]
    span = (upper - lower).replace(0, np.nan)
    position = ((close - lower) / span).clip(0, 1)
    return position.fillna(0.5)


# ============ 9. 趋势强度类因子 ============


def _adx_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """ADX趋势强度：ADX上升且方向正确"""
    adx = _col(df, "adx")
    threshold = p["threshold"]
    strong = adx > threshold
    rising = adx > adx.shift(1)
    return (strong & rising).astype(float)


def _adx_strength(df: pd.DataFrame, p: dict) -> pd.Series:
    """ADX强度分级：ADX在不同区间"""
    adx = _col(df, "adx")
    level = p["level"]
    if level <= 20:
        return (adx < 20).astype(float)
    elif level <= 40:
        return ((adx >= 20) & (adx < 40)).astype(float)
    else:
        return (adx >= 40).astype(float)


def _trend_persistence(df: pd.DataFrame, p: dict) -> pd.Series:
    """趋势持续性：连续N日在均线上方"""
    close = _col(df, "close")
    period = int(p["period"])
    ma = close.rolling(period).mean()
    above = (close > ma).astype(float)
    days_above = above.rolling(int(p["days"])).sum()
    return (days_above / int(p["days"])).clip(0, 1)


def _trend_strength(df: pd.DataFrame, p: dict) -> pd.Series:
    """趋势强度：价格相对均线的偏离程度"""
    close = _col(df, "close")
    period = int(p["period"])
    ma = close.rolling(period).mean()
    deviation = ((close - ma) / ma.replace(0, np.nan) * 100).abs()
    return (deviation / p["max_dev"]).clip(0, 1).fillna(0)


def _higher_highs(df: pd.DataFrame, p: dict) -> pd.Series:
    """更高高点：价格创近期新高"""
    close = _col(df, "close")
    period = int(p["period"])
    highest = close.rolling(period).max()
    at_high = close >= highest * (1 - p["tolerance"] / 100)
    return at_high.astype(float)


def _higher_lows(df: pd.DataFrame, p: dict) -> pd.Series:
    """更高低点：低点逐步抬升"""
    low = _col(df, "low")
    period = int(p["period"])
    recent_low = low.rolling(period).min()
    prev_low = low.shift(period).rolling(period).min()
    rising = recent_low > prev_low
    return rising.astype(float)


# ============ 10. 价格位置类因子 ============


def _price_position(df: pd.DataFrame, p: dict) -> pd.Series:
    """区间低位：收盘价处于N日价格区间的相对低位"""
    period = int(p["period"])
    close = _col(df, "close")
    highest = _col(df, "high").rolling(period).max()
    lowest = _col(df, "low").rolling(period).min()
    span = (highest - lowest).replace(0, np.nan)
    position = (close - lowest) / span
    target = p["target"]
    return (1 - (position - target).abs() / max(target, 1 - target)).clip(0, 1).fillna(0)


def _high_52w(df: pd.DataFrame, p: dict) -> pd.Series:
    """逼近区间新高：距离N日最高点越近得分越高"""
    period = int(p["period"])
    close = _col(df, "close")
    highest = _col(df, "high").rolling(period).max()
    dist = (highest - close) / highest.replace(0, np.nan) * 100
    return (1 - (dist / p["max_dist"]).clip(0, 1)).fillna(0)


def _support_bounce(df: pd.DataFrame, p: dict) -> pd.Series:
    """支撑位反弹：价格触及支撑位后反弹"""
    close, low = _col(df, "close"), _col(df, "low")
    period = int(p["period"])
    support = low.rolling(period).min().shift(1)
    tolerance = p["tolerance"] / 100
    touched = low <= support * (1 + tolerance)
    bounced = close > support * (1 + tolerance)
    return (touched & bounced).astype(float)


def _resistance_break(df: pd.DataFrame, p: dict) -> pd.Series:
    """阻力位突破：价格突破前期高点"""
    close = _col(df, "close")
    period = int(p["period"])
    resistance = _col(df, "high").rolling(period).max().shift(1)
    vol_ratio = _col(df, "vol_ratio")
    breakout = close > resistance
    vol_ok = vol_ratio >= p["vol_ratio"]
    return (breakout & vol_ok).astype(float)


def _range_expansion(df: pd.DataFrame, p: dict) -> pd.Series:
    """区间扩张：价格波动区间开始扩张"""
    high, low = _col(df, "high"), _col(df, "low")
    range_size = high - low
    period = int(p["period"])
    range_ma = range_size.rolling(period).mean()
    expanding = range_size > range_ma * p["ratio"]
    close = _col(df, "close")
    up = close > (high + low) / 2
    return (expanding & up).astype(float)


def _fibonacci_retracement(df: pd.DataFrame, p: dict) -> pd.Series:
    """斐波那契回撤：价格在关键回撤位附近"""
    close = _col(df, "close")
    period = int(p["period"])
    high = _col(df, "high").rolling(period).max()
    low = _col(df, "low").rolling(period).min()
    span = (high - low).replace(0, np.nan)
    position = ((close - low) / span).clip(0, 1)
    # 38.2%, 50%, 61.8% 回撤位
    fib_levels = [0.382, 0.5, 0.618]
    tolerance = p["tolerance"] / 100
    near_fib = False
    for fib in fib_levels:
        near_fib = near_fib | ((position - fib).abs() < tolerance)
    return near_fib.astype(float)


def _pivot_support(df: pd.DataFrame, p: dict) -> pd.Series:
    """枢轴点支撑：价格在枢轴点上方"""
    high, low, close = _col(df, "high"), _col(df, "low"), _col(df, "close")
    pivot = (high.shift(1) + low.shift(1) + close.shift(1)) / 3
    above = close > pivot
    dist = ((close - pivot) / pivot.replace(0, np.nan) * 100).clip(0, 3) / 3
    return above.astype(float) * 0.6 + dist.fillna(0) * 0.4


# ============ 11. 形态类因子 ============


def _bullish_engulfing(df: pd.DataFrame, p: dict) -> pd.Series:
    """看涨吞没：阳线完全吞没前一根阴线"""
    open_, close = _col(df, "open"), _col(df, "close")
    prev_open, prev_close = open_.shift(1), close.shift(1)
    bearish_prev = prev_close < prev_open
    bullish_curr = close > open_
    engulf = (close > prev_open) & (open_ < prev_close)
    return (bearish_prev & bullish_curr & engulf).astype(float)


def _hammer(df: pd.DataFrame, p: dict) -> pd.Series:
    """锤子线：下影线长、实体小"""
    open_, high, low, close = _col(df, "open"), _col(df, "high"), _col(df, "low"), _col(df, "close")
    body = (close - open_).abs()
    lower_shadow = pd.concat([open_, close], axis=1).min(axis=1) - low
    upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
    is_hammer = (lower_shadow > body * 2) & (upper_shadow < body * 0.5)
    return is_hammer.astype(float)


def _morning_star(df: pd.DataFrame, p: dict) -> pd.Series:
    """晨星：三根K线反转形态"""
    open_, close = _col(df, "open"), _col(df, "close")
    # 第一根大阴线
    big_bearish = (close.shift(2) < open_.shift(2)) & ((open_.shift(2) - close.shift(2)) / close.shift(2) > 0.02)
    # 第二根小实体
    small_body = (close.shift(1) - open_.shift(1)).abs() / close.shift(1) < 0.01
    # 第三根大阳线
    big_bullish = (close > open_) & ((close - open_) / open_ > 0.02)
    return (big_bearish & small_body & big_bullish).astype(float)


def _three_white_soldiers(df: pd.DataFrame, p: dict) -> pd.Series:
    """三白兵：连续三根阳线且逐步走高"""
    close, open_ = _col(df, "close"), _col(df, "open")
    bullish = close > open_
    three_up = bullish & bullish.shift(1) & bullish.shift(2)
    higher = (close > close.shift(1)) & (close.shift(1) > close.shift(2))
    return (three_up & higher).astype(float)


def _doji(df: pd.DataFrame, p: dict) -> pd.Series:
    """十字星：开盘价接近收盘价"""
    open_, close, high, low = _col(df, "open"), _col(df, "close"), _col(df, "high"), _col(df, "low")
    body = (close - open_).abs()
    total_range = (high - low).replace(0, np.nan)
    is_doji = body / total_range < 0.1
    return is_doji.astype(float)


def _gap_up(df: pd.DataFrame, p: dict) -> pd.Series:
    """跳空高开：开盘价高于前一日最高价"""
    open_, high = _col(df, "open"), _col(df, "high")
    gap = open_ - high.shift(1)
    gap_pct = gap / high.shift(1).replace(0, np.nan) * 100
    return (gap_pct > p["min_gap"]).astype(float) * (gap_pct / p["max_gap"]).clip(0, 1).fillna(0)


def _inside_bar(df: pd.DataFrame, p: dict) -> pd.Series:
    """内包线：当前K线完全在前一根K线内部"""
    high, low = _col(df, "high"), _col(df, "low")
    inside = (high < high.shift(1)) & (low > low.shift(1))
    return inside.astype(float)


def _outside_bar_bullish(df: pd.DataFrame, p: dict) -> pd.Series:
    """外包阳线：当前K线完全包含前一根且收阳"""
    open_, close, high, low = _col(df, "open"), _col(df, "close"), _col(df, "high"), _col(df, "low")
    outside = (high > high.shift(1)) & (low < low.shift(1))
    bullish = close > open_
    return (outside & bullish).astype(float)


# ============ 12. 统计类因子 ============


def _zscore(df: pd.DataFrame, p: dict) -> pd.Series:
    """Z分数：价格偏离均值的标准差数"""
    close = _col(df, "close")
    period = int(p["period"])
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    z = (close - ma) / std.replace(0, np.nan)
    # 负Z分数（超跌）得分高
    threshold = p["threshold"]
    oversold = z < -threshold
    return oversold.astype(float) * 0.7 + ((-z).clip(0, threshold) / threshold).fillna(0) * 0.3


def _percentile_rank(df: pd.DataFrame, p: dict) -> pd.Series:
    """百分位排名：当前价格在历史区间中的位置"""
    close = _col(df, "close")
    period = int(p["period"])
    return _rolling_rank(close, period)


def _mean_reversion(df: pd.DataFrame, p: dict) -> pd.Series:
    """均值回归：价格偏离均线后有回归趋势"""
    close = _col(df, "close")
    period = int(p["period"])
    ma = close.rolling(period).mean()
    deviation = (close - ma) / ma.replace(0, np.nan)
    # 偏离越大，回归得分越高
    turning = deviation < deviation.shift(1)
    below_ma = deviation < 0
    return (turning & below_ma).astype(float)


def _skewness(df: pd.DataFrame, p: dict) -> pd.Series:
    """偏度：收益率分布偏度"""
    close = _col(df, "close")
    period = int(p["period"])
    returns = close.pct_change()
    skew = returns.rolling(period).skew()
    # 正偏度得分高
    return (skew / p["max_skew"]).clip(0, 1).fillna(0)


def _kurtosis(df: pd.DataFrame, p: dict) -> pd.Series:
    """峰度：收益率分布峰度"""
    close = _col(df, "close")
    period = int(p["period"])
    returns = close.pct_change()
    kurt = returns.rolling(period).apply(lambda x: x.kurtosis(), raw=False)
    # 低峰度（分布均匀）得分高
    return (1 - (kurt / p["max_kurt"]).clip(0, 1)).fillna(0)


def _trend_quality(df: pd.DataFrame, p: dict) -> pd.Series:
    """趋势质量：上涨日占比"""
    close = _col(df, "close")
    period = int(p["period"])
    up_days = (close > close.shift(1)).rolling(period).sum()
    return (up_days / period).clip(0, 1)


def _autocorrelation(df: pd.DataFrame, p: dict) -> pd.Series:
    """自相关：收益率自相关性"""
    close = _col(df, "close")
    period = int(p["period"])
    returns = close.pct_change()
    autocorr = returns.rolling(period).apply(lambda x: x.autocorr(lag=1) if len(x) > 1 else 0, raw=False)
    # 正自相关（趋势延续）得分高
    return autocorr.clip(0, 1).fillna(0)


# ============ 13. 复合类因子 ============


def _trend_follow(df: pd.DataFrame, p: dict) -> pd.Series:
    """趋势跟随：均线+MACD+ADX综合确认"""
    close = _col(df, "close")
    ma20 = close.rolling(20).mean()
    dif, dea = _col(df, "dif"), _col(df, "dea")
    adx = _col(df, "adx")
    above_ma = (close > ma20).astype(float)
    macd_bull = (dif > dea).astype(float)
    adx_strong = (adx > 20).astype(float)
    return above_ma * 0.4 + macd_bull * 0.3 + adx_strong * 0.3


def _oversold_bounce(df: pd.DataFrame, p: dict) -> pd.Series:
    """超跌反弹：RSI+KDJ+BOLL综合超卖"""
    rsi6 = _col(df, "rsi6")
    k, d = _col(df, "k"), _col(df, "d")
    close, lower = _col(df, "close"), _col(df, "boll_lower")
    rsi_oversold = (rsi6 < 30).astype(float)
    kdj_oversold = ((k < 30) & (d < 30)).astype(float)
    near_lower = (close <= lower * 1.02).astype(float)
    return rsi_oversold * 0.4 + kdj_oversold * 0.3 + near_lower * 0.3


def _volume_price_trend(df: pd.DataFrame, p: dict) -> pd.Series:
    """量价趋势：量能+价格+动量综合"""
    close = _col(df, "close")
    vol_ratio = _col(df, "vol_ratio")
    rsi14 = _col(df, "rsi14")
    price_up = (close > close.shift(1)).astype(float)
    vol_up = (vol_ratio > 1.2).astype(float)
    rsi_ok = ((rsi14 > 40) & (rsi14 < 70)).astype(float)
    return price_up * 0.4 + vol_up * 0.3 + rsi_ok * 0.3


def _breakout_confirm(df: pd.DataFrame, p: dict) -> pd.Series:
    """突破确认：价格+量能+指标三重确认"""
    close = _col(df, "close")
    high_20 = _col(df, "high").rolling(20).max()
    vol_ratio = _col(df, "vol_ratio")
    macd = _col(df, "macd")
    price_break = (close > high_20.shift(1)).astype(float)
    vol_confirm = (vol_ratio > 1.5).astype(float)
    macd_confirm = (macd > 0).astype(float)
    return price_break * 0.5 + vol_confirm * 0.25 + macd_confirm * 0.25


def _momentum_quality(df: pd.DataFrame, p: dict) -> pd.Series:
    """动量质量：动量+趋势+量能综合"""
    close = _col(df, "close")
    period = int(p["period"])
    mom = close.pct_change(period)
    vol_ratio = _col(df, "vol_ratio")
    rsi14 = _col(df, "rsi14")
    mom_ok = (mom > 0).astype(float)
    vol_ok = (vol_ratio > 1).astype(float)
    rsi_ok = (rsi14 > 50).astype(float)
    return mom_ok * 0.4 + vol_ok * 0.3 + rsi_ok * 0.3


def _smart_money(df: pd.DataFrame, p: dict) -> pd.Series:
    """聪明资金：MFI+OBV+量比综合"""
    mfi = _col(df, "mfi")
    obv = _col(df, "obv")
    vol_ratio = _col(df, "vol_ratio")
    mfi_rising = (mfi > mfi.shift(1)).astype(float)
    obv_rising = (obv > obv.shift(3)).astype(float)
    vol_active = (vol_ratio > 1).astype(float)
    return mfi_rising * 0.4 + obv_rising * 0.3 + vol_active * 0.3


def _low_risk_entry(df: pd.DataFrame, p: dict) -> pd.Series:
    """低风险入场：波动低+位置好+趋势稳"""
    atr, close = _col(df, "atr14"), _col(df, "close")
    atr_ratio = atr / close * 100
    low_vol = (atr_ratio < p["atr_threshold"]).astype(float)
    ma20 = close.rolling(20).mean()
    near_ma = ((close - ma20).abs() / ma20 < 0.03).astype(float)
    adx = _col(df, "adx")
    stable = ((adx > 15) & (adx < 35)).astype(float)
    return low_vol * 0.4 + near_ma * 0.3 + stable * 0.3


def _streak_score(up: pd.Series, period: int) -> pd.Series:
    """连续同向 streak 计数（遇反向重置为 0），按 period 归一化到 0~1"""
    streak = np.zeros(len(up))
    cur = 0
    for i, v in enumerate(up.fillna(False).to_numpy()):
        cur = cur + 1 if v else 0
        streak[i] = cur
    return (pd.Series(streak, index=up.index) / period).clip(0, 1)


def _consecutive_up(df: pd.DataFrame, p: dict) -> pd.Series:
    """连涨天数：连续上涨天数"""
    close = _col(df, "close")
    period = int(p["period"])
    up = close > close.shift(1)
    return _streak_score(up, period)


def _consecutive_down(df: pd.DataFrame, p: dict) -> pd.Series:
    """连跌天数：连续下跌后反弹概率"""
    close = _col(df, "close")
    period = int(p["period"])
    down = close < close.shift(1)
    # 连跌越多，反弹概率越高
    return _streak_score(down, period)


def _price_acceleration(df: pd.DataFrame, p: dict) -> pd.Series:
    """价格加速度：价格变化的二阶导数"""
    close = _col(df, "close")
    period = int(p["period"])
    returns = close.pct_change()
    accel = returns - returns.shift(period)
    return (accel.clip(0, 0.1) / 0.1).fillna(0)


def _volume_price_correlation(df: pd.DataFrame, p: dict) -> pd.Series:
    """量价相关性：价格和成交量的滚动相关"""
    close = _col(df, "close")
    volume = _col(df, "volume")
    period = int(p["period"])
    corr = close.rolling(period).corr(volume)
    # 正相关（价涨量增）得分高
    return corr.clip(0, 1).fillna(0)


def _rsi_kdj_combined(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI+KDJ组合：两个指标同时发出信号"""
    rsi14 = _col(df, "rsi14")
    k, d = _col(df, "k"), _col(df, "d")
    rsi_ok = (rsi14 > 40) & (rsi14 < 70)
    kdj_ok = k > d
    both_rising = (rsi14 > rsi14.shift(1)) & (k > k.shift(1))
    return (rsi_ok & kdj_ok & both_rising).astype(float)


def _macd_rsi_divergence(df: pd.DataFrame, p: dict) -> pd.Series:
    """MACD+RSI双背离：两个指标同时背离"""
    close = _col(df, "close")
    dif = _col(df, "dif")
    rsi14 = _col(df, "rsi14")
    period = int(p["period"])
    price_low = close.rolling(period).min()
    dif_low = dif.rolling(period).min()
    rsi_low = rsi14.rolling(period).min()
    price_new_low = close <= price_low * 1.02
    dif_higher = dif > dif_low + 0.01
    rsi_higher = rsi14 > rsi_low + 3
    return (price_new_low & dif_higher & rsi_higher).astype(float)


def _multi_timeframe_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """多周期动量：短期和中期动量同向"""
    close = _col(df, "close")
    short = int(p["short"])
    long = int(p["long"])
    mom_short = close.pct_change(short)
    mom_long = close.pct_change(long)
    both_positive = (mom_short > 0) & (mom_long > 0)
    short_stronger = mom_short > mom_long
    return (both_positive & short_stronger).astype(float)


def _volume_breakout_confirm(df: pd.DataFrame, p: dict) -> pd.Series:
    """量能突破确认：成交量突破均线且价格配合"""
    volume = _col(df, "volume")
    close = _col(df, "close")
    period = int(p["period"])
    vol_ma = volume.rolling(period).mean()
    vol_break = volume > vol_ma * p["ratio"]
    price_up = close > close.shift(1)
    return (vol_break & price_up).astype(float)


def _trend_reversal(df: pd.DataFrame, p: dict) -> pd.Series:
    """趋势反转：趋势指标显示可能反转"""
    close = _col(df, "close")
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    was_below = close.shift(5) < ma60.shift(5)
    now_above = close > ma20
    return (was_below & now_above).astype(float)


def _breakout_strength(df: pd.DataFrame, p: dict) -> pd.Series:
    """突破强度：突破幅度和量能的综合"""
    close = _col(df, "close")
    high_20 = _col(df, "high").rolling(20).max()
    vol_ratio = _col(df, "vol_ratio")
    breakout_pct = ((close - high_20.shift(1)) / high_20.shift(1) * 100).clip(0, 5) / 5
    vol_strength = ((vol_ratio - 1) / 2).clip(0, 1)
    return breakout_pct * 0.6 + vol_strength.fillna(0) * 0.4


# ============ 因子注册表 ============

_FACTORS: dict[str, Factor] = {}
_CURRENT_CATEGORY = ""


def _set_category(name: str) -> None:
    """标记后续 _register 的因子所属分类（按注册节顺序）"""
    global _CURRENT_CATEGORY
    _CURRENT_CATEGORY = name


def _register(factor: Factor) -> None:
    factor.category = _CURRENT_CATEGORY
    _FACTORS[factor.id] = factor


# ── 1. 均线类 ─────────────────────────────────
_set_category("均线类 (MA)")

_register(
    Factor(
        id="ma_trend",
        name="均线多头排列",
        description="短期均线在长期均线上方且向上发散",
        params={"fast": FactorParam(5, 3, 20, 1), "slow": FactorParam(20, 10, 60, 5)},
        compute=_ma_trend,
    )
)
_register(
    Factor(
        id="ma_cross",
        name="均线金叉",
        description="短期均线上穿长期均线",
        params={
            "fast": FactorParam(5, 3, 15, 1),
            "slow": FactorParam(10, 10, 60, 5),
            "hold_days": FactorParam(5, 1, 15, 1),
        },
        compute=_ma_cross,
    )
)
_register(
    Factor(
        id="ma_support",
        name="均线支撑",
        description="价格回踩均线不破",
        params={"period": FactorParam(20, 5, 60, 5), "tolerance": FactorParam(2.0, 0.5, 5.0, 0.5)},
        compute=_ma_support,
    )
)
_register(
    Factor(
        id="ma_slope",
        name="均线斜率",
        description="均线持续上升",
        params={
            "period": FactorParam(20, 10, 60, 5),
            "slope_period": FactorParam(5, 2, 10, 1),
            "max_slope": FactorParam(5.0, 2.0, 10.0, 1.0),
        },
        compute=_ma_slope,
    )
)
_register(
    Factor(
        id="ma_compression",
        name="均线收敛",
        description="多条均线收敛后即将发散",
        params={"threshold": FactorParam(3.0, 1.0, 5.0, 0.5)},
        compute=_ma_compression,
    )
)
_register(
    Factor(
        id="ema_trend",
        name="EMA趋势",
        description="价格在EMA上方",
        params={"period": FactorParam(20, 10, 50, 5)},
        compute=_ema_trend,
    )
)
_register(
    Factor(
        id="price_ma_dist",
        name="价格偏离均线",
        description="价格偏离均线的合理范围",
        params={
            "period": FactorParam(20, 10, 60, 5),
            "ideal_dist": FactorParam(2.0, 0.0, 5.0, 0.5),
            "max_dev": FactorParam(10.0, 5.0, 20.0, 2.5),
        },
        compute=_price_ma_distance,
    )
)
_register(
    Factor(
        id="double_ma",
        name="双均线确认",
        description="快慢均线同时向上",
        params={"fast": FactorParam(5, 3, 15, 1), "slow": FactorParam(20, 10, 60, 5)},
        compute=_double_ma_cross,
    )
)
_register(Factor(id="triple_ma", name="三均线确认", description="短>中>长且全部上升", params={}, compute=_triple_ma))

# ── 2. MACD 类 ────────────────────────────────
_set_category("MACD 类")

_register(
    Factor(
        id="macd_cross",
        name="MACD金叉",
        description="DIF上穿DEA",
        params={"hold_days": FactorParam(5, 1, 15, 1)},
        compute=_macd_cross,
    )
)
_register(
    Factor(id="macd_trend", name="MACD趋势走强", description="DIF在DEA上方且柱体放大", params={}, compute=_macd_trend)
)
_register(
    Factor(id="macd_hist", name="MACD柱体", description="柱体由负转正或持续放大", params={}, compute=_macd_histogram)
)
_register(
    Factor(
        id="macd_zero",
        name="MACD零轴穿越",
        description="DIF上穿零轴",
        params={"hold_days": FactorParam(5, 1, 15, 1)},
        compute=_macd_zero_cross,
    )
)
_register(
    Factor(
        id="macd_div",
        name="MACD底背离",
        description="价格新低但MACD不创新低",
        params={"period": FactorParam(20, 10, 40, 5), "tolerance": FactorParam(2.0, 0.5, 5.0, 0.5)},
        compute=_macd_divergence,
    )
)
_register(
    Factor(id="macd_mom", name="MACD动量", description="DIF和DEA同向且差距扩大", params={}, compute=_macd_momentum)
)

# ── 3. RSI 类 ─────────────────────────────────
_set_category("RSI 类")

_register(
    Factor(
        id="rsi_oversold",
        name="RSI超卖反转",
        description="RSI6从超卖区回升",
        params={"threshold": FactorParam(30, 15, 40, 5)},
        compute=_rsi_oversold,
    )
)
_register(
    Factor(
        id="rsi_momentum",
        name="RSI强势区",
        description="RSI14处于强势区但未超买",
        params={"low": FactorParam(50, 40, 60, 5), "high": FactorParam(70, 60, 85, 5)},
        compute=_rsi_momentum,
    )
)
_register(
    Factor(id="rsi_bullish", name="RSI看涨", description="RSI从低位上升且突破50", params={}, compute=_rsi_bullish)
)
_register(
    Factor(
        id="rsi_div",
        name="RSI底背离",
        description="价格新低但RSI不创新低",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_rsi_divergence,
    )
)
_register(
    Factor(
        id="rsi_range",
        name="RSI适中区间",
        description="RSI在中性偏强区间",
        params={"low": FactorParam(40, 30, 50, 5), "high": FactorParam(65, 55, 75, 5)},
        compute=_rsi_range,
    )
)
_register(
    Factor(
        id="rsi_trend",
        name="RSI趋势上升",
        description="RSI持续走高",
        params={"period": FactorParam(10, 5, 20, 5)},
        compute=_rsi_trend,
    )
)
_register(
    Factor(
        id="stoch_rsi",
        name="随机RSI",
        description="RSI的随机指标",
        params={"period": FactorParam(14, 7, 28, 7), "threshold": FactorParam(0.2, 0.1, 0.4, 0.1)},
        compute=_stoch_rsi,
    )
)

# ── 4. KDJ 类 ─────────────────────────────────
_set_category("KDJ 类")

_register(
    Factor(
        id="kdj_cross",
        name="KDJ金叉",
        description="K上穿D",
        params={"low_level": FactorParam(50, 20, 60, 10)},
        compute=_kdj_cross,
    )
)
_register(
    Factor(
        id="kdj_oversold",
        name="KDJ超卖",
        description="K和D都在低位",
        params={"threshold": FactorParam(30, 15, 40, 5)},
        compute=_kdj_oversold,
    )
)
_register(
    Factor(
        id="kdj_golden",
        name="KDJ低位金叉",
        description="J从极低位回升",
        params={"threshold": FactorParam(10, 0, 30, 5)},
        compute=_kdj_golden,
    )
)
_register(Factor(id="kdj_mom", name="KDJ动量", description="J值持续上升", params={}, compute=_kdj_momentum))
_register(Factor(id="kdj_bullish", name="KDJ看涨", description="K>D且J>D且全部上升", params={}, compute=_kdj_bullish))

# ── 5. 布林带类 ───────────────────────────────
_set_category("布林带类 (BOLL)")

_register(
    Factor(
        id="boll_breakout",
        name="布林上轨突破",
        description="放量突破布林上轨",
        params={"vol_ratio": FactorParam(1.5, 1.0, 3.0, 0.5)},
        compute=_boll_breakout,
    )
)
_register(
    Factor(
        id="boll_bounce",
        name="布林下轨反弹",
        description="触及布林下轨后收回",
        params={"tolerance": FactorParam(1.0, 0.0, 3.0, 0.5)},
        compute=_boll_bounce,
    )
)
_register(
    Factor(
        id="boll_squeeze",
        name="布林收窄",
        description="带宽收窄后即将突破",
        params={"threshold": FactorParam(8.0, 4.0, 15.0, 1.0)},
        compute=_boll_squeeze,
    )
)
_register(
    Factor(
        id="boll_position",
        name="布林位置",
        description="价格在布林带中的相对位置",
        params={"ideal": FactorParam(0.5, 0.3, 0.7, 0.1)},
        compute=_boll_position,
    )
)
_register(
    Factor(
        id="boll_mid_sup",
        name="布林中轨支撑",
        description="价格在中轨上方且中轨上升",
        params={},
        compute=_boll_mid_support,
    )
)

# ── 6. 量能类 ─────────────────────────────────
_set_category("量能类 (VOL)")

_register(
    Factor(
        id="vol_breakout",
        name="放量上涨",
        description="量比放大且涨幅达标",
        params={"vol_ratio": FactorParam(2.0, 1.2, 4.0, 0.4), "pct": FactorParam(3.0, 1.0, 6.0, 0.5)},
        compute=_volume_breakout,
    )
)
_register(
    Factor(
        id="obv_trend",
        name="OBV量能上行",
        description="OBV在其均线上方且均线上行",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_obv_trend,
    )
)
_register(
    Factor(
        id="vol_trend",
        name="量能趋势",
        description="成交量持续放大",
        params={"period": FactorParam(10, 5, 20, 5)},
        compute=_volume_trend,
    )
)
_register(
    Factor(
        id="vol_price_confirm",
        name="量价配合",
        description="价涨量增、价跌量缩",
        params={},
        compute=_volume_price_confirm,
    )
)
_register(
    Factor(
        id="vol_shrink",
        name="缩量回调",
        description="价格回调但成交量萎缩",
        params={"period": FactorParam(10, 5, 20, 5), "ratio": FactorParam(0.7, 0.3, 0.9, 0.1)},
        compute=_volume_shrink,
    )
)
_register(
    Factor(
        id="mfi_signal",
        name="MFI资金流",
        description="MFI从超卖区回升",
        params={"threshold": FactorParam(30, 15, 40, 5)},
        compute=_mfi_signal,
    )
)
_register(
    Factor(
        id="mfi_div",
        name="MFI背离",
        description="价格下跌但MFI上升",
        params={"period": FactorParam(10, 5, 20, 5)},
        compute=_mfi_divergence,
    )
)
_register(Factor(id="vwap_support", name="VWAP支撑", description="价格在VWAP上方", params={}, compute=_vwap_support))
_register(
    Factor(
        id="vwap_bounce",
        name="VWAP回踩",
        description="价格回踩VWAP后反弹",
        params={"tolerance": FactorParam(1.0, 0.3, 3.0, 0.3)},
        compute=_vwap_bounce,
    )
)
_register(
    Factor(
        id="vol_spike",
        name="成交量异动",
        description="成交量突然放大",
        params={"period": FactorParam(10, 5, 20, 5), "ratio": FactorParam(2.0, 1.5, 4.0, 0.5)},
        compute=_volume_spike,
    )
)
_register(
    Factor(
        id="accumulation",
        name="吸筹信号",
        description="价格低位但量能持续",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_accumulation,
    )
)

# ── 7. 动量类 ─────────────────────────────────
_set_category("动量类 (MOM)")

_register(
    Factor(
        id="momentum",
        name="N日动量",
        description="区间涨幅归一化",
        params={"period": FactorParam(20, 5, 60, 5), "scale": FactorParam(20.0, 10.0, 40.0, 5.0)},
        compute=_momentum,
    )
)
_register(
    Factor(
        id="roc",
        name="变化率ROC",
        description="价格变化率",
        params={"period": FactorParam(10, 5, 30, 5), "scale": FactorParam(15.0, 5.0, 30.0, 5.0)},
        compute=_roc,
    )
)
_register(
    Factor(
        id="mom_accel",
        name="动量加速度",
        description="动量在加速",
        params={"period": FactorParam(10, 5, 20, 5)},
        compute=_momentum_acceleration,
    )
)
_register(
    Factor(
        id="multi_roc",
        name="多周期ROC",
        description="短期动量强于长期",
        params={"short": FactorParam(5, 3, 10, 1), "long": FactorParam(20, 10, 40, 5)},
        compute=_rate_of_change,
    )
)
_register(
    Factor(
        id="mom_reversal",
        name="动量反转",
        description="前期超跌后动量转正",
        params={"period": FactorParam(10, 5, 20, 5), "threshold": FactorParam(10.0, 5.0, 20.0, 5.0)},
        compute=_momentum_reversal,
    )
)
_register(
    Factor(
        id="williams_r",
        name="威廉指标",
        description="%R从超卖区回升",
        params={"threshold": FactorParam(-80, -95, -65, 5)},
        compute=_williams_r,
    )
)
_register(
    Factor(
        id="cci_signal",
        name="CCI信号",
        description="CCI从超卖区回升",
        params={"threshold": FactorParam(-100, -200, -50, 25)},
        compute=_cci_signal,
    )
)
_register(
    Factor(
        id="cci_trend",
        name="CCI趋势",
        description="CCI在强势区间",
        params={"low": FactorParam(0, -100, 50, 25), "high": FactorParam(200, 100, 300, 50)},
        compute=_cci_trend,
    )
)
_register(
    Factor(
        id="mom_consist",
        name="动量一致性",
        description="连续多日上涨",
        params={"period": FactorParam(5, 3, 10, 1)},
        compute=_momentum_consistency,
    )
)

# ── 8. 波动类 ─────────────────────────────────
_set_category("波动类 (VOLAT)")

_register(
    Factor(
        id="atr_vol",
        name="低波动优选",
        description="ATR占价格比越低得分越高",
        params={"max_ratio": FactorParam(5.0, 2.0, 8.0, 1.0)},
        compute=_atr_volatility,
    )
)
_register(
    Factor(
        id="atr_expand",
        name="ATR扩张",
        description="波动率开始放大",
        params={"period": FactorParam(20, 10, 40, 5), "ratio": FactorParam(1.2, 1.0, 2.0, 0.1)},
        compute=_atr_expansion,
    )
)
_register(
    Factor(
        id="vol_contract",
        name="波动收缩",
        description="波动率持续收缩后即将爆发",
        params={"period": FactorParam(20, 10, 40, 5), "ratio": FactorParam(0.8, 0.5, 0.95, 0.05)},
        compute=_volatility_contraction,
    )
)
_register(
    Factor(
        id="realized_vol",
        name="实际波动率",
        description="近期波动率适中",
        params={
            "period": FactorParam(20, 10, 40, 5),
            "ideal": FactorParam(20.0, 10.0, 40.0, 5.0),
            "max_dev": FactorParam(20.0, 10.0, 40.0, 5.0),
        },
        compute=_realized_vol,
    )
)
_register(
    Factor(
        id="vol_regime",
        name="波动率状态",
        description="低波动环境",
        params={"short": FactorParam(10, 5, 20, 5), "long": FactorParam(30, 20, 60, 10)},
        compute=_volatility_regime,
    )
)
_register(
    Factor(
        id="boll_width",
        name="布林带宽",
        description="带宽收窄",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_boll_width,
    )
)
_register(
    Factor(
        id="keltner_pos",
        name="Keltner通道位置",
        description="价格在通道中轨上方",
        params={"period": FactorParam(20, 10, 40, 5), "mult": FactorParam(2.0, 1.0, 3.0, 0.5)},
        compute=_keltner_position,
    )
)

# ── 9. 趋势强度类 ─────────────────────────────
_set_category("趋势强度类 (TREND)")

_register(
    Factor(
        id="adx_trend",
        name="ADX趋势强度",
        description="ADX上升且方向正确",
        params={"threshold": FactorParam(20, 15, 35, 5)},
        compute=_adx_trend,
    )
)
_register(
    Factor(
        id="adx_strength",
        name="ADX强度分级",
        description="ADX在不同区间",
        params={"level": FactorParam(25, 15, 45, 10)},
        compute=_adx_strength,
    )
)
_register(
    Factor(
        id="trend_persist",
        name="趋势持续性",
        description="连续N日在均线上方",
        params={"period": FactorParam(20, 10, 60, 5), "days": FactorParam(5, 3, 10, 1)},
        compute=_trend_persistence,
    )
)
_register(
    Factor(
        id="trend_strength",
        name="趋势强度",
        description="价格相对均线的偏离程度",
        params={"period": FactorParam(20, 10, 60, 5), "max_dev": FactorParam(10.0, 5.0, 20.0, 2.5)},
        compute=_trend_strength,
    )
)
_register(
    Factor(
        id="higher_highs",
        name="更高高点",
        description="价格创近期新高",
        params={"period": FactorParam(20, 10, 60, 5), "tolerance": FactorParam(1.0, 0.0, 3.0, 0.5)},
        compute=_higher_highs,
    )
)
_register(
    Factor(
        id="higher_lows",
        name="更高低点",
        description="低点逐步抬升",
        params={"period": FactorParam(10, 5, 30, 5)},
        compute=_higher_lows,
    )
)

# ── 10. 价格位置类 ────────────────────────────
_set_category("价格位置类 (POS)")

_register(
    Factor(
        id="price_position",
        name="区间低位",
        description="收盘价处于N日价格区间的相对低位",
        params={"period": FactorParam(60, 20, 120, 10), "target": FactorParam(0.3, 0.1, 0.5, 0.1)},
        compute=_price_position,
    )
)
_register(
    Factor(
        id="high_52w",
        name="逼近区间新高",
        description="距离N日最高点越近得分越高",
        params={"period": FactorParam(250, 60, 250, 10), "max_dist": FactorParam(10.0, 3.0, 20.0, 1.0)},
        compute=_high_52w,
    )
)
_register(
    Factor(
        id="support_bounce",
        name="支撑位反弹",
        description="价格触及支撑位后反弹",
        params={"period": FactorParam(20, 10, 40, 5), "tolerance": FactorParam(2.0, 0.5, 5.0, 0.5)},
        compute=_support_bounce,
    )
)
_register(
    Factor(
        id="resist_break",
        name="阻力位突破",
        description="价格突破前期高点",
        params={"period": FactorParam(20, 10, 60, 5), "vol_ratio": FactorParam(1.5, 1.0, 3.0, 0.5)},
        compute=_resistance_break,
    )
)
_register(
    Factor(
        id="range_expand",
        name="区间扩张",
        description="价格波动区间开始扩张",
        params={"period": FactorParam(10, 5, 20, 5), "ratio": FactorParam(1.5, 1.0, 2.5, 0.5)},
        compute=_range_expansion,
    )
)
_register(
    Factor(
        id="fibonacci",
        name="斐波那契回撤",
        description="价格在关键回撤位附近",
        params={"period": FactorParam(60, 20, 120, 10), "tolerance": FactorParam(3.0, 1.0, 5.0, 1.0)},
        compute=_fibonacci_retracement,
    )
)
_register(
    Factor(id="pivot_support", name="枢轴点支撑", description="价格在枢轴点上方", params={}, compute=_pivot_support)
)

# ── 11. 形态类 ────────────────────────────────
_set_category("形态类 (PATTERN)")

_register(
    Factor(
        id="bull_engulf", name="看涨吞没", description="阳线完全吞没前一根阴线", params={}, compute=_bullish_engulfing
    )
)
_register(Factor(id="hammer", name="锤子线", description="下影线长、实体小", params={}, compute=_hammer))
_register(Factor(id="morning_star", name="晨星", description="三根K线反转形态", params={}, compute=_morning_star))
_register(
    Factor(
        id="three_white", name="三白兵", description="连续三根阳线且逐步走高", params={}, compute=_three_white_soldiers
    )
)
_register(Factor(id="doji", name="十字星", description="开盘价接近收盘价", params={}, compute=_doji))
_register(
    Factor(
        id="gap_up",
        name="跳空高开",
        description="开盘价高于前一日最高价",
        params={"min_gap": FactorParam(1.0, 0.5, 3.0, 0.5), "max_gap": FactorParam(5.0, 3.0, 10.0, 1.0)},
        compute=_gap_up,
    )
)
_register(
    Factor(id="inside_bar", name="内包线", description="当前K线完全在前一根K线内部", params={}, compute=_inside_bar)
)
_register(
    Factor(
        id="outside_bull",
        name="外包阳线",
        description="当前K线完全包含前一根且收阳",
        params={},
        compute=_outside_bar_bullish,
    )
)

# ── 12. 统计类 ────────────────────────────────
_set_category("统计类 (STAT)")

_register(
    Factor(
        id="zscore",
        name="Z分数",
        description="价格偏离均值的标准差数",
        params={"period": FactorParam(20, 10, 40, 5), "threshold": FactorParam(1.5, 0.5, 3.0, 0.5)},
        compute=_zscore,
    )
)
_register(
    Factor(
        id="pct_rank",
        name="百分位排名",
        description="当前价格在历史区间中的位置",
        params={"period": FactorParam(60, 20, 120, 10)},
        compute=_percentile_rank,
    )
)
_register(
    Factor(
        id="mean_revert",
        name="均值回归",
        description="价格偏离均线后有回归趋势",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_mean_reversion,
    )
)
_register(
    Factor(
        id="skewness",
        name="偏度",
        description="收益率分布偏度",
        params={"period": FactorParam(20, 10, 40, 5), "max_skew": FactorParam(1.0, 0.5, 2.0, 0.5)},
        compute=_skewness,
    )
)
_register(
    Factor(
        id="kurtosis",
        name="峰度",
        description="收益率分布峰度",
        params={"period": FactorParam(20, 10, 40, 5), "max_kurt": FactorParam(5.0, 2.0, 10.0, 1.0)},
        compute=_kurtosis,
    )
)
_register(
    Factor(
        id="trend_quality",
        name="趋势质量",
        description="上涨日占比",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_trend_quality,
    )
)
_register(
    Factor(
        id="autocorr",
        name="自相关",
        description="收益率自相关性",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_autocorrelation,
    )
)

# ── 13. 复合类 ────────────────────────────────
_set_category("复合类 (COMP)")

_register(
    Factor(id="trend_follow", name="趋势跟随", description="均线+MACD+ADX综合确认", params={}, compute=_trend_follow)
)
_register(
    Factor(
        id="oversold_bounce", name="超跌反弹", description="RSI+KDJ+BOLL综合超卖", params={}, compute=_oversold_bounce
    )
)
_register(
    Factor(
        id="vol_price_trend", name="量价趋势", description="量能+价格+动量综合", params={}, compute=_volume_price_trend
    )
)
_register(
    Factor(
        id="breakout_confirm",
        name="突破确认",
        description="价格+量能+指标三重确认",
        params={},
        compute=_breakout_confirm,
    )
)
_register(
    Factor(
        id="mom_quality",
        name="动量质量",
        description="动量+趋势+量能综合",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_momentum_quality,
    )
)
_register(Factor(id="smart_money", name="聪明资金", description="MFI+OBV+量比综合", params={}, compute=_smart_money))
_register(
    Factor(
        id="low_risk_entry",
        name="低风险入场",
        description="波动低+位置好+趋势稳",
        params={"atr_threshold": FactorParam(3.0, 1.5, 5.0, 0.5)},
        compute=_low_risk_entry,
    )
)
_register(
    Factor(
        id="consecutive_up",
        name="连涨天数",
        description="连续上涨天数",
        params={"period": FactorParam(5, 3, 10, 1)},
        compute=_consecutive_up,
    )
)
_register(
    Factor(
        id="consecutive_down",
        name="连跌反弹",
        description="连续下跌后反弹概率",
        params={"period": FactorParam(5, 3, 10, 1)},
        compute=_consecutive_down,
    )
)
_register(
    Factor(
        id="price_accel",
        name="价格加速度",
        description="价格变化的二阶导数",
        params={"period": FactorParam(5, 3, 10, 1)},
        compute=_price_acceleration,
    )
)
_register(
    Factor(
        id="vol_price_corr",
        name="量价相关性",
        description="价格和成交量的滚动相关",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_volume_price_correlation,
    )
)
_register(
    Factor(
        id="rsi_kdj_combo", name="RSI+KDJ组合", description="两个指标同时发出信号", params={}, compute=_rsi_kdj_combined
    )
)
_register(
    Factor(
        id="macd_rsi_div",
        name="MACD+RSI双背离",
        description="两个指标同时背离",
        params={"period": FactorParam(20, 10, 40, 5)},
        compute=_macd_rsi_divergence,
    )
)
_register(
    Factor(
        id="multi_tf_mom",
        name="多周期动量",
        description="短期和中期动量同向",
        params={"short": FactorParam(5, 3, 10, 1), "long": FactorParam(20, 10, 40, 5)},
        compute=_multi_timeframe_momentum,
    )
)
_register(
    Factor(
        id="vol_break_confirm",
        name="量能突破确认",
        description="成交量突破均线且价格配合",
        params={"period": FactorParam(20, 10, 40, 5), "ratio": FactorParam(1.5, 1.0, 3.0, 0.5)},
        compute=_volume_breakout_confirm,
    )
)
_register(
    Factor(id="trend_reversal", name="趋势反转", description="趋势指标显示可能反转", params={}, compute=_trend_reversal)
)
_register(
    Factor(
        id="breakout_strength",
        name="突破强度",
        description="突破幅度和量能的综合",
        params={},
        compute=_breakout_strength,
    )
)


# ============ 公共 API ============


def list_factors() -> list[Factor]:
    """返回全部因子列表"""
    return list(_FACTORS.values())


def list_factors_catalog() -> list[dict]:
    """按分类分组返回因子目录（id/名称/描述/参数搜索范围），供 Agent 写 logic_json 前查阅"""
    catalog: list[dict] = []
    seen: list[str] = []
    for f in _FACTORS.values():
        if f.category not in seen:
            seen.append(f.category)
    for cat in seen:
        items = []
        for f in _FACTORS.values():
            if f.category != cat:
                continue
            items.append(
                {
                    "id": f.id,
                    "name": f.name,
                    "description": f.description,
                    "params": {
                        k: {"default": p.default, "min": p.min, "max": p.max, "step": p.step}
                        for k, p in f.params.items()
                    },
                }
            )
        catalog.append({"category": cat, "factors": items})
    return catalog


def get_factor(factor_id: str) -> Factor:
    """按 id 获取因子，未知 id 抛 KeyError（错误信息不罗列全部 id，请用 list_factors 工具查阅）"""
    if factor_id not in _FACTORS:
        raise KeyError(f"未知因子: {factor_id}（用 list_factors 工具查看全部可用因子 id）")
    return _FACTORS[factor_id]
