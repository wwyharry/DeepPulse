"""回测验证框架 - 战法历史信号回测、绩效统计、胜率/盈亏比/最大回撤"""
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np

from src.query import StockQuery


# ============ 数据结构 ============

@dataclass
class Trade:
    """单笔交易记录"""
    code: str
    name: str = ""
    entry_date: str = ""
    entry_price: float = 0.0
    exit_date: str = ""
    exit_price: float = 0.0
    direction: str = "long"  # long / short
    shares: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    signal: str = ""
    exit_reason: str = ""


@dataclass
class BacktestResult:
    """回测结果"""
    strategy_name: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_hold_days: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    expectancy: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("equity_curve", None)
        return d

    def summary(self) -> str:
        """生成可读的回测摘要"""
        lines = [
            f"=== 回测报告: {self.strategy_name} ===",
            f"回测区间: {self.start_date} ~ {self.end_date}",
            f"初始资金: {self.initial_capital:,.0f}",
            f"最终资金: {self.final_capital:,.0f}",
            f"总收益率: {self.total_return:.2%}",
            f"年化收益: {self.annual_return:.2%}",
            f"最大回撤: {self.max_drawdown:.2%}",
            f"最大回撤持续: {self.max_drawdown_duration} 笔交易",
            f"",
            f"总交易次数: {self.total_trades}",
            f"胜率: {self.win_rate:.1%}",
            f"盈利因子: {self.profit_factor:.2f}",
            f"夏普比率: {self.sharpe_ratio:.2f}",
            f"期望收益: {self.expectancy:.2%}",
            f"",
            f"盈利笔数: {self.winning_trades} | 平均盈利: {self.avg_win:.2%}",
            f"亏损笔数: {self.losing_trades} | 平均亏损: {self.avg_loss:.2%}",
            f"平均持仓: {self.avg_hold_days:.1f} 天",
            f"最大连赢: {self.max_consecutive_wins} | 最大连亏: {self.max_consecutive_losses}",
        ]
        return "\n".join(lines)


# ============ 技术指标计算 ============

def calc_ma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    dif = ema_fast - ema_slow
    dea = calc_ema(dif, signal)
    macd = (dif - dea) * 2
    return dif, dea, macd


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
             n: int = 9, m1: int = 3, m2: int = 3):
    lowest_low = low.rolling(window=n, min_periods=n).min()
    highest_high = high.rolling(window=n, min_periods=n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1/m1, adjust=False).mean()
    d = k.ewm(alpha=1/m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def calc_boll(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = calc_ma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff())
    obv = (volume * direction).cumsum()
    return obv


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """为K线DataFrame添加常用技术指标"""
    df = df.copy()
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # 均线
    for p in [5, 10, 20, 60]:
        df[f"ma{p}"] = calc_ma(c, p)

    # MACD
    df["dif"], df["dea"], df["macd"] = calc_macd(c)

    # RSI
    df["rsi6"] = calc_rsi(c, 6)
    df["rsi14"] = calc_rsi(c, 14)

    # KDJ
    df["k"], df["d"], df["j"] = calc_kdj(h, l, c)

    # 布林带
    df["boll_upper"], df["boll_mid"], df["boll_lower"] = calc_boll(c)

    # ATR
    df["atr14"] = calc_atr(h, l, c, 14)

    # OBV
    df["obv"] = calc_obv(c, v)

    # 量比（当日成交量 / 前5日均量）
    df["vol_ma5"] = calc_ma(v, 5)
    df["vol_ratio"] = v / df["vol_ma5"].replace(0, np.nan)

    # 涨跌幅
    df["pct_change"] = c.pct_change() * 100

    return df


# ============ 内置信号函数 ============

def signal_ma_cross(df: pd.DataFrame, fast: int = 5, slow: int = 10) -> pd.Series:
    """均线金叉/死叉信号: 1=金叉买入, -1=死叉卖出"""
    fast_ma = df[f"ma{fast}"]
    slow_ma = df[f"ma{slow}"]
    cross_up = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
    cross_down = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))
    signal = pd.Series(0, index=df.index)
    signal[cross_up] = 1
    signal[cross_down] = -1
    return signal


def signal_macd_cross(df: pd.DataFrame) -> pd.Series:
    """MACD金叉/死叉信号"""
    cross_up = (df["dif"] > df["dea"]) & (df["dif"].shift(1) <= df["dea"].shift(1))
    cross_down = (df["dif"] < df["dea"]) & (df["dif"].shift(1) >= df["dea"].shift(1))
    signal = pd.Series(0, index=df.index)
    signal[cross_up] = 1
    signal[cross_down] = -1
    return signal


def signal_rsi_oversold(df: pd.DataFrame, buy_threshold: int = 30,
                        sell_threshold: int = 70) -> pd.Series:
    """RSI超卖买入/超买卖出"""
    buy = (df["rsi6"] < buy_threshold) & (df["rsi6"].shift(1) >= buy_threshold)
    sell = (df["rsi6"] > sell_threshold) & (df["rsi6"].shift(1) <= sell_threshold)
    signal = pd.Series(0, index=df.index)
    signal[buy] = 1
    signal[sell] = -1
    return signal


def signal_volume_breakout(df: pd.DataFrame, vol_ratio_threshold: float = 2.0,
                           pct_threshold: float = 3.0) -> pd.Series:
    """放量突破信号: 成交量>均量N倍 且 涨幅>M%"""
    buy = (df["vol_ratio"] > vol_ratio_threshold) & (df["pct_change"] > pct_threshold)
    sell = (df["vol_ratio"] > vol_ratio_threshold) & (df["pct_change"] < -pct_threshold)
    signal = pd.Series(0, index=df.index)
    signal[buy] = 1
    signal[sell] = -1
    return signal


def signal_boll_bounce(df: pd.DataFrame) -> pd.Series:
    """布林带支撑/压力反弹信号"""
    buy = (df["low"] <= df["boll_lower"]) & (df["close"] > df["boll_lower"])
    sell = (df["high"] >= df["boll_upper"]) & (df["close"] < df["boll_upper"])
    signal = pd.Series(0, index=df.index)
    signal[buy] = 1
    signal[sell] = -1
    return signal


def signal_kdj_cross(df: pd.DataFrame) -> pd.Series:
    """KDJ金叉/死叉（J值从负转正区域）"""
    cross_up = (df["j"] > 0) & (df["j"].shift(1) <= 0) & (df["j"] < 50)
    cross_down = (df["j"] < 100) & (df["j"].shift(1) >= 100)
    signal = pd.Series(0, index=df.index)
    signal[cross_up] = 1
    signal[cross_down] = -1
    return signal


def signal_multi_confirm(df: pd.DataFrame) -> pd.Series:
    """多指标共振信号: 至少3个指标同方向"""
    ma_bull = (df["ma5"] > df["ma10"]).astype(int) * 2 - 1  # +1 / -1
    macd_bull = (df["dif"] > df["dea"]).astype(int) * 2 - 1
    rsi_bull = (df["rsi6"] > 50).astype(int) * 2 - 1
    kdj_bull = (df["j"] > 50).astype(int) * 2 - 1

    score = ma_bull + macd_bull + rsi_bull + kdj_bull
    buy = (score >= 4) & (score.shift(1) < 4)
    sell = (score <= -2) & (score.shift(1) > -2)

    signal = pd.Series(0, index=df.index)
    signal[buy] = 1
    signal[sell] = -1
    return signal


# 内置信号函数注册表
SIGNAL_FUNCTIONS = {
    "ma_cross": signal_ma_cross,
    "macd_cross": signal_macd_cross,
    "rsi_oversold": signal_rsi_oversold,
    "volume_breakout": signal_volume_breakout,
    "boll_bounce": signal_boll_bounce,
    "kdj_cross": signal_kdj_cross,
    "multi_confirm": signal_multi_confirm,
}


# ============ 回测引擎 ============

class BacktestEngine:
    """回测引擎"""

    def __init__(self, initial_capital: float = 100000, commission: float = 0.001,
                 slippage: float = 0.001, position_pct: float = 0.95,
                 stop_loss_pct: float = 0.05, take_profit_pct: float = 0.10,
                 max_hold_days: int = 20):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.position_pct = position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_days = max_hold_days

    def run(self, df: pd.DataFrame, signal_func, strategy_name: str = "策略",
            code: str = "", name: str = "") -> BacktestResult:
        """
        运行回测

        Args:
            df: 带技术指标的K线DataFrame
            signal_func: 信号函数，接收df返回Series(1=买,-1=卖,0=无信号)
            strategy_name: 策略名称
            code: 股票代码
            name: 股票名称
        """
        if len(df) < 30:
            return BacktestResult(
                strategy_name=strategy_name,
                start_date=str(df["trade_date"].iloc[0]) if len(df) > 0 else "",
                end_date=str(df["trade_date"].iloc[-1]) if len(df) > 0 else "",
                initial_capital=self.initial_capital,
                final_capital=self.initial_capital,
            )

        signals = signal_func(df)
        df = df.copy()
        df["signal"] = signals

        capital = self.initial_capital
        position = 0
        entry_price = 0.0
        entry_date = ""
        entry_idx = 0
        trades = []
        equity_curve = []

        for i in range(len(df)):
            row = df.iloc[i]
            current_price = row["close"]
            current_date = str(row["trade_date"])

            # 计算当前权益
            if position > 0:
                equity = capital + position * current_price
            else:
                equity = capital
            equity_curve.append({"date": current_date, "equity": equity})

            # 持仓中：检查止损/止盈/超时
            if position > 0:
                pnl_pct = (current_price - entry_price) / entry_price
                hold_days = i - entry_idx

                exit_reason = None
                if pnl_pct <= -self.stop_loss_pct:
                    exit_reason = "止损"
                elif pnl_pct >= self.take_profit_pct:
                    exit_reason = "止盈"
                elif hold_days >= self.max_hold_days:
                    exit_reason = "超时"

                if exit_reason or row["signal"] == -1:
                    if not exit_reason:
                        exit_reason = "信号卖出"
                    sell_price = current_price * (1 - self.slippage)
                    proceeds = position * sell_price * (1 - self.commission)
                    pnl = proceeds - position * entry_price
                    pnl_pct_actual = (sell_price - entry_price) / entry_price

                    trades.append(Trade(
                        code=code, name=name,
                        entry_date=entry_date, entry_price=round(entry_price, 3),
                        exit_date=current_date, exit_price=round(sell_price, 3),
                        direction="long", shares=position,
                        pnl=round(pnl, 2), pnl_pct=round(pnl_pct_actual, 4),
                        hold_days=hold_days,
                        signal=strategy_name,
                        exit_reason=exit_reason,
                    ))

                    capital = proceeds + capital - position * entry_price * (1 + self.commission)
                    # 修正：capital = 原capital - 买入成本 + 卖出所得
                    capital = self._recalc_capital(capital, position, entry_price, sell_price)
                    position = 0

            # 无持仓：检查买入信号
            if position == 0 and row["signal"] == 1:
                buy_price = current_price * (1 + self.slippage)
                buy_cost = capital * self.position_pct
                shares = int(buy_cost / (buy_price * (1 + self.commission)) / 100) * 100
                if shares >= 100:
                    cost = shares * buy_price * (1 + self.commission)
                    capital -= cost
                    position = shares
                    entry_price = buy_price
                    entry_date = current_date
                    entry_idx = i

        # 如果回测结束仍持仓，按最后价格平仓
        if position > 0:
            last_row = df.iloc[-1]
            sell_price = last_row["close"] * (1 - self.slippage)
            proceeds = position * sell_price * (1 - self.commission)
            pnl_pct_actual = (sell_price - entry_price) / entry_price
            trades.append(Trade(
                code=code, name=name,
                entry_date=entry_date, entry_price=round(entry_price, 3),
                exit_date=str(last_row["trade_date"]), exit_price=round(sell_price, 3),
                direction="long", shares=position,
                pnl=round(proceeds - position * entry_price, 2),
                pnl_pct=round(pnl_pct_actual, 4),
                hold_days=len(df) - 1 - entry_idx,
                signal=strategy_name,
                exit_reason="回测结束平仓",
            ))
            capital += proceeds
            position = 0

        return self._compute_metrics(trades, equity_curve, strategy_name,
                                     str(df["trade_date"].iloc[0]),
                                     str(df["trade_date"].iloc[-1]))

    def _recalc_capital(self, capital, shares, entry_price, sell_price):
        """重新计算卖出后的资金"""
        # 简化：买入时已扣资金，卖出时加回
        return capital + shares * sell_price * (1 - self.commission)

    def _compute_metrics(self, trades: list, equity_curve: list,
                         strategy_name: str, start_date: str,
                         end_date: str) -> BacktestResult:
        """计算回测绩效指标"""
        result = BacktestResult(
            strategy_name=strategy_name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
        )

        if not trades:
            return result

        result.trades = [asdict(t) for t in trades]
        result.equity_curve = equity_curve
        result.total_trades = len(trades)

        # 盈亏统计
        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.win_rate = len(wins) / len(trades) if trades else 0
        result.avg_win = np.mean(wins) if wins else 0
        result.avg_loss = np.mean(losses) if losses else 0

        # 盈利因子
        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 0.001
        result.profit_factor = total_win / total_loss if total_loss > 0 else float("inf")

        # 最终权益
        final_equity = equity_curve[-1]["equity"] if equity_curve else self.initial_capital
        result.final_capital = round(final_equity, 2)
        result.total_return = (final_equity - self.initial_capital) / self.initial_capital

        # 年化收益
        if len(equity_curve) >= 2:
            days = len(equity_curve)
            years = days / 244  # 交易日
            if years > 0 and final_equity > 0:
                result.annual_return = (final_equity / self.initial_capital) ** (1 / years) - 1

        # 最大回撤
        equities = [e["equity"] for e in equity_curve]
        peak = equities[0]
        max_dd = 0
        dd_duration = 0
        current_dd_streak = 0
        for eq in equities:
            if eq > peak:
                peak = eq
                current_dd_streak = 0
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
            if dd > 0:
                current_dd_streak += 1
                dd_duration = max(dd_duration, current_dd_streak)
        result.max_drawdown = max_dd
        result.max_drawdown_duration = dd_duration

        # 夏普比率（假设无风险利率3%）
        if len(pnls) > 1:
            returns = np.array(pnls)
            excess_returns = returns - 0.03 / 244
            std = np.std(excess_returns, ddof=1)
            result.sharpe_ratio = np.mean(excess_returns) / std * np.sqrt(244) if std > 0 else 0

        # 期望收益
        result.expectancy = np.mean(pnls) if pnls else 0

        # 平均持仓天数
        result.avg_hold_days = np.mean([t.hold_days for t in trades])

        # 最大连胜/连亏
        max_win_streak = 0
        max_loss_streak = 0
        current_win = 0
        current_loss = 0
        for p in pnls:
            if p > 0:
                current_win += 1
                current_loss = 0
                max_win_streak = max(max_win_streak, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss_streak = max(max_loss_streak, current_loss)
        result.max_consecutive_wins = max_win_streak
        result.max_consecutive_losses = max_loss_streak

        return result


# ============ 便捷函数 ============

def backtest_stock(code: str, strategy: str = "macd_cross", days: int = 500,
                   initial_capital: float = 100000, stop_loss: float = 0.05,
                   take_profit: float = 0.10, max_hold: int = 20,
                   **signal_kwargs) -> BacktestResult:
    """
    对单只股票运行指定策略的回测

    Args:
        code: 股票代码
        strategy: 策略名（ma_cross / macd_cross / rsi_oversold / volume_breakout /
                  boll_bounce / kdj_cross / multi_confirm）
        days: 回测数据天数（交易日）
        initial_capital: 初始资金
        stop_loss: 止损比例
        take_profit: 止盈比例
        max_hold: 最大持仓天数
        signal_kwargs: 传递给信号函数的额外参数
    """
    query = StockQuery()
    df = query.get_daily_kline(code, limit=days)

    if df.empty:
        return BacktestResult(
            strategy_name=strategy,
            start_date="", end_date="",
            initial_capital=initial_capital,
            final_capital=initial_capital,
        )

    # 获取股票名称
    name = ""
    info = query.get_stock_info(code)
    if not info.empty:
        name = info.iloc[0].get("name", "")

    # 添加技术指标
    df = add_indicators(df)

    # 获取信号函数
    signal_func = SIGNAL_FUNCTIONS.get(strategy)
    if not signal_func:
        raise ValueError(f"未知策略: {strategy}，可用: {list(SIGNAL_FUNCTIONS.keys())}")

    # 如果有额外参数，包装信号函数
    if signal_kwargs:
        original_func = signal_func
        def wrapped(df):
            return original_func(df, **signal_kwargs)
        signal_func = wrapped

    engine = BacktestEngine(
        initial_capital=initial_capital,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
        max_hold_days=max_hold,
    )

    return engine.run(df, signal_func, strategy_name=strategy, code=code, name=name)


def backtest_strategy_multi_stock(strategy: str = "macd_cross",
                                   codes: list[str] = None,
                                   days: int = 500,
                                   top_n: int = 10,
                                   **kwargs) -> dict:
    """
    在多只股票上回测同一策略，汇总统计

    Args:
        strategy: 策略名
        codes: 股票代码列表，None则从数据库随机抽样
        days: 回测天数
        top_n: 返回表现最好的N只
    """
    if codes is None:
        query = StockQuery()
        all_stocks = query.get_stock_info()
        if all_stocks.empty:
            return {"error": "数据库无股票数据"}
        codes = all_stocks["code"].sample(min(100, len(all_stocks))).tolist()

    results = []
    for code in codes:
        try:
            r = backtest_stock(code, strategy=strategy, days=days, **kwargs)
            if r.total_trades > 0:
                results.append({
                    "code": code,
                    "name": r.trades[0].get("name", "") if r.trades else "",
                    "total_return": r.total_return,
                    "win_rate": r.win_rate,
                    "total_trades": r.total_trades,
                    "profit_factor": r.profit_factor,
                    "max_drawdown": r.max_drawdown,
                    "sharpe_ratio": r.sharpe_ratio,
                })
        except Exception:
            continue

    if not results:
        return {"error": "回测无有效结果"}

    # 按收益率排序
    results.sort(key=lambda x: x["total_return"], reverse=True)

    # 汇总统计
    all_returns = [r["total_return"] for r in results]
    all_win_rates = [r["win_rate"] for r in results if r["total_trades"] > 0]

    return {
        "strategy": strategy,
        "tested_stocks": len(results),
        "top_stocks": results[:top_n],
        "avg_return": round(np.mean(all_returns), 4),
        "median_return": round(np.median(all_returns), 4),
        "avg_win_rate": round(np.mean(all_win_rates), 4) if all_win_rates else 0,
        "positive_rate": round(sum(1 for r in all_returns if r > 0) / len(all_returns), 4),
    }
