"""信号生成器 - 基于 StrategySpec 的量化因子信号生成

核心方法 generate_signals_for_strategy() 接收一个已保存的个股AI策略
（StockStrategy），用其 StrategySpec 计算各因子得分，综合判断是否
触发买入/卖出信号。
"""

from datetime import date, timedelta

import pandas as pd

from deeppulse.strategy.models import Signal, SignalType, StockStrategy


class SignalGenerator:
    """信号生成器 - 基于 StrategySpec 生成交易信号"""

    def __init__(self):
        pass

    def generate_signals_for_strategy(
        self,
        stock_strategy: StockStrategy,
        days: int = 60,
    ) -> list[Signal]:
        """为一个已保存的个股策略生成信号

        核心逻辑：加载K线 → 计算技术指标 → 用 factor_scores_detail
        逐 bar 计算因子得分 → 超过阈值则生成信号。

        只对「已入库最大 signal_time 之后」的 bar 生成信号，历史触发
        只入库一次，之后扫描不再作为新信号重复产生。
        """
        from datetime import datetime

        from deeppulse.strategy.spec import factor_scores_detail

        signals = []

        try:
            df = self._load_kline(stock_strategy.stock_code, days)
            if df is None or df.empty:
                return signals

            from deeppulse.agent.indicators import TechnicalIndicators

            df = TechnicalIndicators.add_all(df)
            spec = stock_strategy.get_spec()

            # 扫描窗口过滤：跳过已入库信号时间及之前的 bar
            from deeppulse.strategy.repository import get_repository

            max_saved = get_repository().get_max_signal_time(stock_strategy.id, stock_strategy.stock_code)
            max_saved_rank = 0.0
            if max_saved is not None:
                try:
                    if isinstance(max_saved, datetime):
                        max_saved_rank = max_saved.timestamp()
                    else:
                        max_saved_rank = datetime.fromisoformat(str(max_saved).replace(" ", "T", 1)).timestamp()
                except (ValueError, TypeError):
                    max_saved_rank = 0.0

            # 检查最近 5 个交易日
            for i in range(max(0, len(df) - 5), len(df)):
                sub_df = df.iloc[: i + 1]
                if len(sub_df) < 30:  # 需要足够的数据计算指标
                    continue

                trade_date = df.iloc[i]["trade_date"]
                bar_rank = 0.0
                try:
                    if isinstance(trade_date, datetime):
                        bar_rank = trade_date.timestamp()
                    elif hasattr(trade_date, "isoformat"):
                        bar_rank = datetime.fromisoformat(trade_date.isoformat()).timestamp()
                    else:
                        bar_rank = datetime.fromisoformat(str(trade_date).replace(" ", "T", 1)).timestamp()
                except (ValueError, TypeError):
                    bar_rank = 0.0
                if max_saved_rank and bar_rank <= max_saved_rank:
                    continue  # 历史触发已入库，不再重复生成

                try:
                    detail = factor_scores_detail(spec, sub_df)
                except Exception:
                    continue

                if detail["triggered"]:
                    signals.append(
                        Signal(
                            strategy_id=stock_strategy.id,
                            strategy_name=stock_strategy.name,
                            stock_code=stock_strategy.stock_code,
                            stock_name=stock_strategy.stock_name,
                            signal_type=SignalType.BUY,
                            signal_price=float(df.iloc[i]["close"]),
                            signal_time=str(trade_date),
                            confidence=round(detail["total_score"], 4),
                            matched_conditions=[f["factor_name"] for f in detail["factors"] if f["score"] > 0.5],
                        )
                    )

        except Exception as e:
            print(f"生成信号失败 {stock_strategy.stock_code}: {e}")

        return signals

    def scan_all_stock_strategies(self, stock_codes: list[str] = None) -> list[Signal]:
        """扫描所有已保存的个股策略，生成信号（仅返回实际新入库的信号）"""
        from deeppulse.strategy.repository import get_repository

        repo = get_repository()
        all_signals = []

        if stock_codes:
            strategies = []
            for code in stock_codes:
                strategies.extend(repo.list_stock_strategies(stock_code=code))
        else:
            strategies = repo.list_stock_strategies()

        for ss in strategies:
            try:
                signals = self.generate_signals_for_strategy(ss)
                for signal in signals:
                    # 仅当实际新插入时才计入，避免重复通知/重复计数
                    if repo.save_signal(signal):
                        all_signals.append(signal)
            except Exception as e:
                print(f"扫描失败 {ss.stock_code}/{ss.name}: {e}")
                continue

        return all_signals

    def _load_kline(self, stock_code: str, days: int) -> pd.DataFrame | None:
        """加载K线数据"""
        try:
            from deeppulse.src.query import StockQuery

            query = StockQuery()
            end = date.today()
            start = end - timedelta(days=days * 3)
            df = query.get_daily_kline(stock_code, start_date=str(start), end_date=str(end))
            if df is None or df.empty:
                return None
            return df.sort_values("trade_date").reset_index(drop=True)
        except Exception:
            return None

    def get_latest_signals(self, limit: int = 50) -> list[Signal]:
        """获取最新信号"""
        from deeppulse.strategy.repository import get_repository

        return get_repository().get_signals(limit=limit)

    def get_strategy_signals(self, strategy_id: str, limit: int = 50) -> list[Signal]:
        """获取特定策略的信号"""
        from deeppulse.strategy.repository import get_repository

        return get_repository().get_signals(strategy_id=strategy_id, limit=limit)


# 全局实例
_generator = None


def get_signal_generator() -> SignalGenerator:
    """获取全局信号生成器实例"""
    global _generator
    if _generator is None:
        _generator = SignalGenerator()
    return _generator
