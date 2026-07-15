"""统一技术指标计算引擎

所有指标计算集中在此模块，消除 backtest.py 和 tools/market.py 的重复实现。
"""

import numpy as np
import pandas as pd


class TechnicalIndicators:
    """统一技术指标计算引擎"""

    # ── 基础指标 ──────────────────────────────────────

    @staticmethod
    def ma(series: pd.Series, period: int) -> pd.Series:
        """简单移动平均"""
        return series.rolling(window=period, min_periods=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """指数移动平均"""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """相对强弱指标（EWM 平滑）"""
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        """MACD (DIF, DEA, MACD柱)"""
        ema_fast = TechnicalIndicators.ema(close, fast)
        ema_slow = TechnicalIndicators.ema(close, slow)
        dif = ema_fast - ema_slow
        dea = TechnicalIndicators.ema(dif, signal)
        macd = (dif - dea) * 2
        return dif, dea, macd

    @staticmethod
    def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
            n: int = 9, m1: int = 3, m2: int = 3):
        """KDJ 指标"""
        lowest_low = low.rolling(window=n, min_periods=n).min()
        highest_high = high.rolling(window=n, min_periods=n).max()
        rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
        k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
        d = k.ewm(alpha=1 / m2, adjust=False).mean()
        j = 3 * k - 2 * d
        return k, d, j

    @staticmethod
    def boll(close: pd.Series, period: int = 20, std_dev: float = 2.0):
        """布林带 (上轨, 中轨, 下轨)"""
        mid = TechnicalIndicators.ma(close, period)
        std = close.rolling(window=period, min_periods=period).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        return upper, mid, lower

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """平均真实波幅"""
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(window=period, min_periods=period).mean()

    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """能量潮"""
        direction = np.sign(close.diff())
        return (volume * direction).cumsum()

    # ── 新增指标 ──────────────────────────────────────

    @staticmethod
    def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """平均趋向指数（趋势强度，>25 为强趋势）"""
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        atr_val = TechnicalIndicators.atr(high, low, close, period)
        plus_di = 100 * TechnicalIndicators.ema(plus_dm, period) / atr_val.replace(0, np.nan)
        minus_di = 100 * TechnicalIndicators.ema(minus_dm, period) / atr_val.replace(0, np.nan)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = TechnicalIndicators.ema(dx, period)
        return adx

    @staticmethod
    def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """威廉指标 (-100~0, <-80 超卖, >-20 超买)"""
        highest = high.rolling(window=period, min_periods=period).max()
        lowest = low.rolling(window=period, min_periods=period).min()
        wr = -100 * (highest - close) / (highest - lowest).replace(0, np.nan)
        return wr

    @staticmethod
    def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """商品通道指数"""
        tp = (high + low + close) / 3
        tp_ma = tp.rolling(window=period, min_periods=period).mean()
        tp_std = tp.rolling(window=period, min_periods=period).std()
        return (tp - tp_ma) / (0.015 * tp_std).replace(0, np.nan)

    @staticmethod
    def mfi(high: pd.Series, low: pd.Series, close: pd.Series,
            volume: pd.Series, period: int = 14) -> pd.Series:
        """资金流量指标（类似带成交量的 RSI）"""
        tp = (high + low + close) / 3
        mf = tp * volume
        tp_diff = tp.diff()
        pos_mf = mf.where(tp_diff > 0, 0.0).rolling(window=period).sum()
        neg_mf = mf.where(tp_diff <= 0, 0.0).rolling(window=period).sum()
        mfr = pos_mf / neg_mf.replace(0, np.nan)
        return 100 - (100 / (1 + mfr))

    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """成交量加权平均价"""
        tp = (high + low + close) / 3
        cum_tp_vol = (tp * volume).cumsum()
        cum_vol = volume.cumsum()
        return cum_tp_vol / cum_vol.replace(0, np.nan)

    @staticmethod
    def williams_alligator(high: pd.Series, low: pd.Series):
        """一目均衡表（简化版：转换线/基准线/先行带）"""
        mid = (high + low) / 2
        tenkan = (mid.rolling(9).max() + mid.rolling(9).min()) / 2    # 转换线
        kijun = (mid.rolling(26).max() + mid.rolling(26).min()) / 2   # 基准线
        senkou_a = ((tenkan + kijun) / 2).shift(26)                    # 先行带A
        senkou_b = ((mid.rolling(52).max() + mid.rolling(52).min()) / 2).shift(26)  # 先行带B
        return tenkan, kijun, senkou_a, senkou_b

    # ── 批量计算 ──────────────────────────────────────

    @staticmethod
    def add_all(df: pd.DataFrame) -> pd.DataFrame:
        """一次性计算所有指标，返回增强后的 DataFrame"""
        df = df.copy()
        c = df["close"]
        h = df["high"]
        l = df["low"]  # noqa: E741
        v = df["volume"]

        # 均线
        for p in [5, 10, 20, 60]:
            df[f"ma{p}"] = TechnicalIndicators.ma(c, p)

        # MACD
        df["dif"], df["dea"], df["macd"] = TechnicalIndicators.macd(c)

        # RSI
        df["rsi6"] = TechnicalIndicators.rsi(c, 6)
        df["rsi14"] = TechnicalIndicators.rsi(c, 14)

        # KDJ
        df["k"], df["d"], df["j"] = TechnicalIndicators.kdj(h, l, c)

        # 布林带
        df["boll_upper"], df["boll_mid"], df["boll_lower"] = TechnicalIndicators.boll(c)

        # ATR
        df["atr14"] = TechnicalIndicators.atr(h, l, c, 14)

        # OBV
        df["obv"] = TechnicalIndicators.obv(c, v)

        # 量比
        df["vol_ma5"] = TechnicalIndicators.ma(v, 5)
        df["vol_ratio"] = v / df["vol_ma5"].replace(0, np.nan)

        # 涨跌幅
        df["pct_change"] = c.pct_change() * 100

        # ── 新增指标 ──
        df["adx"] = TechnicalIndicators.adx(h, l, c)
        df["williams_r"] = TechnicalIndicators.williams_r(h, l, c)
        df["cci"] = TechnicalIndicators.cci(h, l, c)
        df["mfi"] = TechnicalIndicators.mfi(h, l, c, v)
        df["vwap"] = TechnicalIndicators.vwap(h, l, c, v)

        return df

    # ── 信号解读 ──────────────────────────────────────

    @staticmethod
    def interpret_signals(df: pd.DataFrame) -> dict:
        """解读最新一行指标值，返回信号字典"""
        if df.empty:
            return {}

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        signals = {}

        # 均线排列
        if all(pd.notna(last.get(f"ma{p}")) for p in [5, 10, 20, 60]):
            if last["ma5"] > last["ma10"] > last["ma20"] > last["ma60"]:
                signals["ma_alignment"] = "多头排列"
            elif last["ma5"] < last["ma10"] < last["ma20"] < last["ma60"]:
                signals["ma_alignment"] = "空头排列"
            else:
                signals["ma_alignment"] = "交叉整理"

        # MACD
        if pd.notna(last.get("dif")) and pd.notna(prev.get("dif")):
            if prev["dif"] <= prev["dea"] and last["dif"] > last["dea"]:
                signals["macd"] = "金叉"
            elif prev["dif"] >= prev["dea"] and last["dif"] < last["dea"]:
                signals["macd"] = "死叉"
            elif last["dif"] > last["dea"]:
                signals["macd"] = "多头"
            else:
                signals["macd"] = "空头"

        # RSI
        rsi = last.get("rsi14")
        if pd.notna(rsi):
            if rsi > 80:
                signals["rsi"] = "超买"
            elif rsi > 70:
                signals["rsi"] = "偏强"
            elif rsi < 20:
                signals["rsi"] = "超卖"
            elif rsi < 30:
                signals["rsi"] = "偏弱"
            else:
                signals["rsi"] = "中性"
            signals["rsi_value"] = round(rsi, 1)

        # KDJ
        j = last.get("j")
        if pd.notna(j):
            if j > 100:
                signals["kdj"] = "超买"
            elif j < 0:
                signals["kdj"] = "超卖"
            elif last.get("k", 0) > last.get("d", 0):
                signals["kdj"] = "多头"
            else:
                signals["kdj"] = "空头"

        # 布林带位置
        if pd.notna(last.get("boll_upper")):
            if last["close"] > last["boll_upper"]:
                signals["boll"] = "突破上轨"
            elif last["close"] < last["boll_lower"]:
                signals["boll"] = "跌破下轨"
            elif last["close"] > last["boll_mid"]:
                signals["boll"] = "中轨上方"
            else:
                signals["boll"] = "中轨下方"

        # ADX 趋势强度
        adx = last.get("adx")
        if pd.notna(adx):
            if adx > 40:
                signals["adx"] = "强趋势"
            elif adx > 25:
                signals["adx"] = "有趋势"
            else:
                signals["adx"] = "无趋势/震荡"
            signals["adx_value"] = round(adx, 1)

        # 量比
        vol_ratio = last.get("vol_ratio")
        if pd.notna(vol_ratio):
            if vol_ratio > 3:
                signals["volume"] = "天量"
            elif vol_ratio > 2:
                signals["volume"] = "放量"
            elif vol_ratio > 1.5:
                signals["volume"] = "温和放量"
            elif vol_ratio < 0.5:
                signals["volume"] = "极度缩量"
            elif vol_ratio < 0.7:
                signals["volume"] = "缩量"
            else:
                signals["volume"] = "正常"

        return signals
