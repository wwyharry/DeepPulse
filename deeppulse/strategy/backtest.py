"""策略回测引擎 - 增强版回测，支持滑点、手续费、参数优化、动态退出策略"""


import numpy as np
import pandas as pd

from deeppulse.agent.indicators import TechnicalIndicators
from deeppulse.src.query import StockQuery
from deeppulse.strategy.exit_strategies import (
    PRESET_EXIT_STRATEGIES,
    DynamicExitEngine,
    ExitStrategy,
    get_preset_strategy,
)
from deeppulse.strategy.models import BacktestResult, Trade

# ============ 技术指标计算（委托统一引擎） ============

calc_ma = TechnicalIndicators.ma
calc_ema = TechnicalIndicators.ema
calc_rsi = TechnicalIndicators.rsi
calc_macd = TechnicalIndicators.macd
calc_kdj = TechnicalIndicators.kdj
calc_boll = TechnicalIndicators.boll
calc_atr = TechnicalIndicators.atr
calc_obv = TechnicalIndicators.obv


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """为K线DataFrame添加常用技术指标"""
    return TechnicalIndicators.add_all(df)


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


def signal_rsi_oversold(df: pd.DataFrame, buy_threshold: int = 30, sell_threshold: int = 70) -> pd.Series:
    """RSI超卖买入/超买卖出"""
    buy = (df["rsi6"] < buy_threshold) & (df["rsi6"].shift(1) >= buy_threshold)
    sell = (df["rsi6"] > sell_threshold) & (df["rsi6"].shift(1) <= sell_threshold)
    signal = pd.Series(0, index=df.index)
    signal[buy] = 1
    signal[sell] = -1
    return signal


def signal_volume_breakout(df: pd.DataFrame, vol_ratio_threshold: float = 2.0, pct_threshold: float = 3.0) -> pd.Series:
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
    ma_bull = (df["ma5"] > df["ma10"]).astype(int) * 2 - 1
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
    """回测引擎 - 支持滑点、手续费、资金管理"""

    def __init__(
        self,
        initial_capital: float = 100000,
        commission: float = 0.001,
        slippage: float = 0.001,
        position_pct: float = 0.95,
        stop_loss_pct: float = 0.05,
        take_profit_pct: float = 0.10,
        max_hold_days: int = 20,
    ):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.position_pct = position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_days = max_hold_days

    def run(
        self,
        df: pd.DataFrame,
        signal_func,
        strategy_name: str = "策略",
        code: str = "",
        name: str = "",
    ) -> BacktestResult:
        """运行回测"""
        if len(df) < 30:
            return BacktestResult(
                strategy_name=strategy_name,
                start_date=str(df["trade_date"].iloc[0]) if len(df) > 0 else "",
                end_date=str(df["trade_date"].iloc[-1]) if len(df) > 0 else "",
                initial_capital=self.initial_capital,
                final_capital=self.initial_capital,
                stock_code=code,
                stock_name=name,
            )

        signals = signal_func(df)
        df = df.copy()
        # 前视偏差修复：信号延迟一根 bar —— 决策发生在第 i-1 根收盘，成交在第 i 根收盘
        df["signal"] = pd.Series(signals, index=df.index).shift(1).fillna(0).astype(int)

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
            exited_this_bar = False  # 同一根 bar 退出后禁止立刻按同一收盘价买回

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
                    # 含佣成本核算：买入侧佣金计入成本，卖出侧佣金计入回款
                    proceeds = position * sell_price * (1 - self.commission)
                    entry_cost = position * entry_price * (1 + self.commission)
                    pnl = proceeds - entry_cost
                    pnl_pct_actual = sell_price * (1 - self.commission) / (entry_price * (1 + self.commission)) - 1

                    trades.append(
                        Trade(
                            code=code,
                            name=name,
                            entry_date=entry_date,
                            entry_price=round(entry_price, 3),
                            exit_date=current_date,
                            exit_price=round(sell_price, 3),
                            direction="long",
                            shares=position,
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl_pct_actual, 4),
                            hold_days=hold_days,
                            signal=strategy_name,
                            exit_reason=exit_reason,
                        )
                    )

                    capital = self._recalc_capital(capital, position, entry_price, sell_price)
                    position = 0
                    exited_this_bar = True

            # 无持仓：检查买入信号（同 bar 退出后不重新入场）
            if position == 0 and row["signal"] == 1 and not exited_this_bar:
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
            entry_cost = position * entry_price * (1 + self.commission)
            pnl_pct_actual = sell_price * (1 - self.commission) / (entry_price * (1 + self.commission)) - 1
            trades.append(
                Trade(
                    code=code,
                    name=name,
                    entry_date=entry_date,
                    entry_price=round(entry_price, 3),
                    exit_date=str(last_row["trade_date"]),
                    exit_price=round(sell_price, 3),
                    direction="long",
                    shares=position,
                    pnl=round(proceeds - entry_cost, 2),
                    pnl_pct=round(pnl_pct_actual, 4),
                    hold_days=len(df) - 1 - entry_idx,
                    signal=strategy_name,
                    exit_reason="回测结束平仓",
                )
            )
            capital += proceeds
            position = 0

        return self._compute_metrics(
            trades,
            equity_curve,
            strategy_name,
            code,
            name,
            str(df["trade_date"].iloc[0]),
            str(df["trade_date"].iloc[-1]),
            final_cash=capital,
        )

    def _recalc_capital(self, capital, shares, entry_price, sell_price):
        """重新计算卖出后的资金"""
        return capital + shares * sell_price * (1 - self.commission)

    def _compute_metrics(
        self,
        trades: list,
        equity_curve: list,
        strategy_name: str,
        code: str,
        name: str,
        start_date: str,
        end_date: str,
        final_cash: float | None = None,
    ) -> BacktestResult:
        """计算回测绩效指标

        Args:
            final_cash: 强平后的真实现金。回测结束仍持仓时，equity_curve[-1]
                是未扣卖出成本的市值点，与平仓交易记录对不上账，应传入强平后 capital。
        """
        result = BacktestResult(
            strategy_name=strategy_name,
            stock_code=code,
            stock_name=name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
        )

        if not trades:
            return result

        result.trades = [t.to_dict() for t in trades]
        result.equity_curve = equity_curve
        result.total_trades = len(trades)

        # 盈亏统计
        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.win_rate = len(wins) / len(trades) if trades else 0
        result.avg_win = float(np.mean(wins)) if wins else 0
        result.avg_loss = float(np.mean(losses)) if losses else 0

        # 盈利因子
        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 0.001
        result.profit_factor = total_win / total_loss if total_loss > 0 else float("inf")

        # 最终权益（优先使用强平后的真实现金）
        if final_cash is not None:
            final_equity = final_cash
        else:
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

        # 夏普比率（日收益口径，无风险利率3%，按244个交易日年化）
        eq_series = pd.Series([e["equity"] for e in equity_curve], dtype=float)
        daily_ret = eq_series.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        if len(daily_ret) >= 2:
            excess = daily_ret - 0.03 / 244
            std = float(excess.std(ddof=1))
            result.sharpe_ratio = float(excess.mean() / std * np.sqrt(244)) if std > 0 else 0

        # 期望收益
        result.expectancy = float(np.mean(pnls)) if pnls else 0

        # 平均持仓天数
        result.avg_hold_days = float(np.mean([t.hold_days for t in trades]))

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


class DynamicBacktestEngine(BacktestEngine):
    """动态退出回测引擎 - 支持ATR止损、移动止损、技术退出

    核心理念: "没有问题就不止盈"
    - ATR止损: 基于市场波动的动态止损
    - 移动止损: 保护利润，跟随最高价
    - 技术退出: 当出现技术问题时才考虑止盈
    """

    def __init__(
        self,
        initial_capital: float = 100000,
        commission: float = 0.001,
        slippage: float = 0.001,
        position_pct: float = 0.95,
        exit_strategy: ExitStrategy = None,
        exit_strategy_preset: str = None,
    ):
        """
        初始化动态退出回测引擎

        Args:
            initial_capital: 初始资金
            commission: 手续费率
            slippage: 滑点率
            position_pct: 仓位比例
            exit_strategy: 退出策略配置（优先使用）
            exit_strategy_preset: 退出策略预设名称 (conservative/balanced/aggressive)
        """
        super().__init__(
            initial_capital=initial_capital,
            commission=commission,
            slippage=slippage,
            position_pct=position_pct,
            stop_loss_pct=0.05,  # 不再使用，保留兼容性
            take_profit_pct=0.10,  # 不再使用，保留兼容性
            max_hold_days=30,
        )

        # 设置退出策略
        if exit_strategy is not None:
            self.exit_strategy = exit_strategy
        elif exit_strategy_preset is not None:
            self.exit_strategy = get_preset_strategy(exit_strategy_preset)
        else:
            self.exit_strategy = get_preset_strategy("balanced")  # 默认平衡型

    def run(
        self,
        df: pd.DataFrame,
        signal_func,
        strategy_name: str = "策略",
        code: str = "",
        name: str = "",
    ) -> BacktestResult:
        """运行回测（使用动态退出策略）"""
        if len(df) < 30:
            return BacktestResult(
                strategy_name=strategy_name,
                start_date=str(df["trade_date"].iloc[0]) if len(df) > 0 else "",
                end_date=str(df["trade_date"].iloc[-1]) if len(df) > 0 else "",
                initial_capital=self.initial_capital,
                final_capital=self.initial_capital,
                stock_code=code,
                stock_name=name,
                exit_strategy_type=self._get_strategy_type(),
            )

        signals = signal_func(df)
        df = df.copy()
        # 前视偏差修复：信号延迟一根 bar —— 决策发生在第 i-1 根收盘，成交在第 i 根收盘
        df["signal"] = pd.Series(signals, index=df.index).shift(1).fillna(0).astype(int)

        # 添加前一日数据用于技术退出判断
        df["dif_prev"] = df["dif"].shift(1)
        df["dea_prev"] = df["dea"].shift(1)
        df["rsi6_prev"] = df["rsi6"].shift(1)
        df["ma20_prev"] = df["ma20"].shift(1)
        df["close_prev"] = df["close"].shift(1)
        # ATR列名可能是atr或atr14，统一为atr
        if "atr14" in df.columns and "atr" not in df.columns:
            df["atr"] = df["atr14"]
        df["atr_prev"] = df["atr"].shift(1)

        capital = self.initial_capital
        position = 0
        entry_price = 0.0
        entry_date = ""
        entry_idx = 0
        trades = []
        equity_curve = []
        exit_statistics = {}  # 退出原因统计
        exit_analysis = {}  # 退出原因分析

        # 动态退出引擎
        exit_engine = DynamicExitEngine(self.exit_strategy)

        for i in range(len(df)):
            row = df.iloc[i]
            row_dict = row.to_dict()
            current_price = row["close"]
            current_date = str(row["trade_date"])
            exited_this_bar = False  # 同一根 bar 全平后禁止立刻按同一收盘价买回

            # 计算当前权益
            if position > 0:
                equity = capital + position * current_price
            else:
                equity = capital
            equity_curve.append({"date": current_date, "equity": equity})

            # 持仓中：检查动态退出条件
            if position > 0:
                hold_days = i - entry_idx
                df_history = df.iloc[max(0, i - 20) : i + 1] if i >= 20 else df.iloc[: i + 1]

                should_exit, exit_reason, exit_ratio = exit_engine.should_exit(
                    row_dict, entry_price, hold_days, df_history
                )

                if should_exit or row["signal"] == -1:
                    # 策略卖出信号强制全平，不能沿用技术部分退出比例
                    if row["signal"] == -1:
                        exit_ratio = 1.0
                        exit_reason = "信号卖出" if not exit_reason else f"{exit_reason}+信号卖出"
                    elif not exit_reason:
                        exit_reason = "信号卖出"

                    # 计算退出比例（分批止盈），钳位到 (0, 1] 防御异常比率超卖持仓
                    exit_ratio = min(max(float(exit_ratio), 0.0), 1.0)
                    exit_shares = int(position * exit_ratio / 100) * 100
                    exit_shares = min(exit_shares, position)
                    if exit_shares < 100:
                        exit_shares = position  # 退出太少时全部退出

                    sell_price = current_price * (1 - self.slippage)
                    # 含佣成本核算：买入侧佣金计入成本，卖出侧佣金计入回款
                    proceeds = exit_shares * sell_price * (1 - self.commission)
                    entry_cost = exit_shares * entry_price * (1 + self.commission)
                    pnl = proceeds - entry_cost
                    pnl_pct_actual = sell_price * (1 - self.commission) / (entry_price * (1 + self.commission)) - 1

                    trades.append(
                        Trade(
                            code=code,
                            name=name,
                            entry_date=entry_date,
                            entry_price=round(entry_price, 3),
                            exit_date=current_date,
                            exit_price=round(sell_price, 3),
                            direction="long",
                            shares=exit_shares,
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl_pct_actual, 4),
                            hold_days=hold_days,
                            signal=strategy_name,
                            exit_reason=exit_reason,
                        )
                    )

                    # 更新退出统计
                    exit_statistics[exit_reason] = exit_statistics.get(exit_reason, 0) + 1

                    # 更新退出原因分析
                    if exit_reason not in exit_analysis:
                        exit_analysis[exit_reason] = {"total_return": 0, "count": 0}
                    exit_analysis[exit_reason]["total_return"] += pnl_pct_actual
                    exit_analysis[exit_reason]["count"] += 1

                    # 更新资金和持仓
                    capital = self._recalc_capital(capital, exit_shares, entry_price, sell_price)
                    position -= exit_shares

                    # 如果全部退出，重置引擎
                    if position <= 0:
                        position = 0
                        exited_this_bar = True
                        exit_engine.reset()

            # 无持仓：检查买入信号（同 bar 全平后不重新入场）
            if position == 0 and row["signal"] == 1 and not exited_this_bar:
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
                    # 重置退出引擎
                    exit_engine = DynamicExitEngine(self.exit_strategy)

        # 如果回测结束仍持仓，按最后价格平仓
        if position > 0:
            last_row = df.iloc[-1]
            sell_price = last_row["close"] * (1 - self.slippage)
            proceeds = position * sell_price * (1 - self.commission)
            entry_cost = position * entry_price * (1 + self.commission)
            pnl_pct_actual = sell_price * (1 - self.commission) / (entry_price * (1 + self.commission)) - 1
            trades.append(
                Trade(
                    code=code,
                    name=name,
                    entry_date=entry_date,
                    entry_price=round(entry_price, 3),
                    exit_date=str(last_row["trade_date"]),
                    exit_price=round(sell_price, 3),
                    direction="long",
                    shares=position,
                    pnl=round(proceeds - entry_cost, 2),
                    pnl_pct=round(pnl_pct_actual, 4),
                    hold_days=len(df) - 1 - entry_idx,
                    signal=strategy_name,
                    exit_reason="回测结束平仓",
                )
            )
            capital += proceeds
            position = 0

        # 计算退出原因平均收益
        for analysis in exit_analysis.values():
            if analysis["count"] > 0:
                analysis["avg_return"] = analysis["total_return"] / analysis["count"]
            else:
                analysis["avg_return"] = 0

        result = self._compute_metrics(
            trades,
            equity_curve,
            strategy_name,
            code,
            name,
            str(df["trade_date"].iloc[0]),
            str(df["trade_date"].iloc[-1]),
            final_cash=capital,
        )

        # 添加退出策略信息
        result.exit_strategy_type = self._get_strategy_type()
        result.exit_statistics = exit_statistics
        result.exit_analysis = exit_analysis

        return result

    def _get_strategy_type(self) -> str:
        """获取退出策略类型名称"""
        for name, strategy in PRESET_EXIT_STRATEGIES.items():
            if strategy.to_dict() == self.exit_strategy.to_dict():
                return name
        return "custom"


# ============ 便捷函数 ============


def backtest_stock(
    code: str,
    strategy: str = "macd_cross",
    days: int = 500,
    initial_capital: float = 100000,
    stop_loss: float = 0.05,
    take_profit: float = 0.10,
    max_hold: int = 20,
    signal_func=None,
    use_dynamic_exit: bool = True,
    exit_strategy_preset: str = "balanced",
    exit_strategy_params: dict = None,
    **signal_kwargs,
) -> BacktestResult:
    """
    对单只股票运行指定策略的回测

    Args:
        code: 股票代码
        strategy: 策略名或自定义信号函数
        days: 回测数据天数（交易日）
        initial_capital: 初始资金
        stop_loss: 止损比例（仅在use_dynamic_exit=False时使用）
        take_profit: 止盈比例（仅在use_dynamic_exit=False时使用）
        max_hold: 最大持仓天数（仅在use_dynamic_exit=False时使用）
        signal_func: 自定义信号函数（优先使用）
        use_dynamic_exit: 是否使用动态退出策略（推荐）
        exit_strategy_preset: 动态退出策略预设 (conservative/balanced/aggressive)
        exit_strategy_params: 自定义退出策略参数（覆盖预设）
        signal_kwargs: 传递给信号函数的额外参数
    """
    query = StockQuery()
    df = query.get_daily_kline(code, limit=days)

    if df.empty:
        return BacktestResult(
            strategy_name=strategy,
            start_date="",
            end_date="",
            initial_capital=initial_capital,
            final_capital=initial_capital,
            stock_code=code,
        )

    # 获取股票名称
    name = ""
    info = query.get_stock_info(code)
    if not info.empty:
        name = info.iloc[0].get("name", "")

    # 添加技术指标
    df = add_indicators(df)

    # 获取信号函数
    if signal_func is None:
        signal_func = SIGNAL_FUNCTIONS.get(strategy)
        if not signal_func:
            raise ValueError(f"未知策略: {strategy}，可用: {list(SIGNAL_FUNCTIONS.keys())}")

    # 如果有额外参数，包装信号函数
    if signal_kwargs:
        original_func = signal_func

        def wrapped(df):
            return original_func(df, **signal_kwargs)

        signal_func = wrapped

    # 选择回测引擎
    if use_dynamic_exit:
        # 构建退出策略
        exit_strategy = None
        if exit_strategy_params:
            # from_dict 过滤未知键；__post_init__ 校验 partial_exit_ratio 范围
            exit_strategy = ExitStrategy.from_dict(exit_strategy_params)

        engine = DynamicBacktestEngine(
            initial_capital=initial_capital,
            exit_strategy=exit_strategy,
            exit_strategy_preset=exit_strategy_preset if not exit_strategy else None,
        )
    else:
        # 使用传统静态止损止盈
        engine = BacktestEngine(
            initial_capital=initial_capital,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            max_hold_days=max_hold,
        )

    return engine.run(df, signal_func, strategy_name=strategy, code=code, name=name)


def backtest_strategy_multi_stock(
    strategy: str = "macd_cross",
    codes: list[str] = None,
    days: int = 500,
    top_n: int = 10,
    use_dynamic_exit: bool = True,
    exit_strategy_preset: str = "balanced",
    **kwargs,
) -> dict:
    """
    在多只股票上回测同一策略，汇总统计

    Args:
        strategy: 策略名称
        codes: 股票代码列表。为空时从全市场随机抽样最多 100 只
            （固定种子 random_state=42，可复现）。零交易股票不计入
            avg_win_rate/avg_return/positive_rate，但会在 stocks_without_trades 中披露。
        days: 回测天数
        top_n: 返回前N只股票
        use_dynamic_exit: 是否使用动态退出策略
        exit_strategy_preset: 动态退出策略预设
        **kwargs: 其他参数
    """
    universe_size = None
    if codes is None:
        query = StockQuery()
        all_stocks = query.get_stock_info()
        if all_stocks.empty:
            return {"error": "数据库无股票数据"}
        universe_size = len(all_stocks)
        # 固定种子保证可复现，同时保持代码分布代表性
        codes = all_stocks["code"].sample(min(100, len(all_stocks)), random_state=42).tolist()

    attempted = len(codes)
    errors = 0
    results = []
    for code in codes:
        try:
            r = backtest_stock(
                code,
                strategy=strategy,
                days=days,
                use_dynamic_exit=use_dynamic_exit,
                exit_strategy_preset=exit_strategy_preset,
                **kwargs,
            )
            if r.total_trades > 0:
                results.append(
                    {
                        "code": code,
                        "name": r.stock_name,
                        "total_return": r.total_return,
                        "win_rate": r.win_rate,
                        "total_trades": r.total_trades,
                        "profit_factor": r.profit_factor,
                        "max_drawdown": r.max_drawdown,
                        "sharpe_ratio": r.sharpe_ratio,
                        "exit_strategy_type": r.exit_strategy_type,
                        "exit_statistics": r.exit_statistics,
                    }
                )
        except Exception:
            errors += 1
            continue

    if not results:
        return {"error": "回测无有效结果"}

    # 按收益率排序
    results.sort(key=lambda x: x["total_return"], reverse=True)

    # 汇总统计（仅统计有交易的股票，零交易样本单独披露）
    all_returns = [r["total_return"] for r in results]
    all_win_rates = [r["win_rate"] for r in results if r["total_trades"] > 0]

    # 汇总退出统计
    total_exit_statistics = {}
    for r in results:
        for reason, count in r.get("exit_statistics", {}).items():
            total_exit_statistics[reason] = total_exit_statistics.get(reason, 0) + count

    stocks_with_trades = len(results)
    stocks_without_trades = attempted - stocks_with_trades - errors

    return {
        "strategy": strategy,
        "exit_strategy": exit_strategy_preset if use_dynamic_exit else "static",
        "tested_stocks": attempted,  # 语义：尝试回测的股票数（含零交易/失败样本）
        "sampled": attempted,
        "universe": universe_size if universe_size is not None else attempted,
        "stocks_with_trades": stocks_with_trades,
        "stocks_without_trades": stocks_without_trades,
        "error_count": errors,
        "coverage": round(stocks_with_trades / attempted, 4) if attempted else 0,
        "top_stocks": results[:top_n],
        # 以下 avg_return/median_return/avg_win_rate/positive_rate 仅统计有交易的股票
        # （win_rate 以交易为条件，零交易样本置 0 会低估，故不并入）
        "avg_return": round(float(np.mean(all_returns)), 4),
        "median_return": round(float(np.median(all_returns)), 4),
        "avg_win_rate": round(float(np.mean(all_win_rates)), 4) if all_win_rates else 0,
        "positive_rate": round(sum(1 for r in all_returns if r > 0) / len(all_returns), 4),
        "positive_rate_all": round(sum(1 for r in all_returns if r > 0) / attempted, 4) if attempted else 0,
        "exit_statistics": total_exit_statistics,
    }


def monte_carlo_simulation(total_return: float, trades: list, n_simulations: int = 1000) -> dict:
    """
    蒙特卡洛模拟

    基于历史交易的收益率分布，模拟未来可能的结果。
    """
    if not trades:
        return {"error": "无交易记录"}

    # 提取每笔交易的收益率
    returns = [t.get("pnl_pct", 0) for t in trades if "pnl_pct" in t]
    if not returns:
        return {"error": "无有效收益率数据"}

    returns = np.array(returns)

    # 模拟
    final_values = []
    for _ in range(n_simulations):
        shuffled = np.random.choice(returns, size=len(returns), replace=True)
        cumulative = np.prod(1 + shuffled)
        final_values.append(cumulative)

    final_values = np.array(final_values)

    # 统计结果
    percentiles = np.percentile(final_values, [5, 25, 50, 75, 95])

    return {
        "n_simulations": n_simulations,
        "n_trades": len(returns),
        "actual_return": round(total_return, 4),
        "median_final": round(float(percentiles[2]), 4),
        "percentile_5": round(float(percentiles[0]), 4),
        "percentile_25": round(float(percentiles[1]), 4),
        "percentile_75": round(float(percentiles[3]), 4),
        "percentile_95": round(float(percentiles[4]), 4),
        "prob_profit": round(float(np.mean(final_values > 1)), 3),
        "prob_loss_10pct": round(float(np.mean(final_values < 0.9)), 3),
        "prob_double": round(float(np.mean(final_values > 2)), 3),
        "var_95": round(float(1 - percentiles[0]), 4),
    }
