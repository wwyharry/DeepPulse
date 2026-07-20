"""信号生成器 - 基于策略条件生成交易信号"""

import re
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

from deeppulse.strategy.models import Signal, SignalType, Strategy
from deeppulse.strategy.repository import get_repository


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全获取浮点数值，处理None和非数值类型"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_get(row: dict, key: str, default: float = 0.0) -> float:
    """安全从row获取浮点数值，处理None和非数值类型"""
    return _safe_float(row.get(key), default)


class SignalGenerator:
    """信号生成器"""

    def __init__(self):
        self.repository = get_repository()

    def generate_signals(
        self,
        strategy: Strategy,
        stock_code: str,
        stock_name: str = "",
        days: int = 60,
    ) -> list[Signal]:
        """为单只股票生成信号"""
        signals = []

        try:
            from deeppulse.src.query import StockQuery

            query = StockQuery()
            end = date.today()
            start = end - timedelta(days=days * 3)

            df = query.get_daily_kline(stock_code, start_date=str(start), end_date=str(end))
            if df.empty:
                return signals

            df = df.sort_values("trade_date").reset_index(drop=True)

            # 添加技术指标
            from deeppulse.agent.indicators import TechnicalIndicators

            df = TechnicalIndicators.add_all(df)

            # 解析策略条件
            conditions = strategy.conditions
            if not conditions:
                from deeppulse.strategy.engine import get_engine

                engine = get_engine()
                conditions = engine.parse_conditions(strategy.content)

            # 检查最近几天的信号
            for i in range(max(0, len(df) - 5), len(df)):
                row = df.iloc[i]
                signal = self._check_signal(row, df, strategy, stock_code, stock_name, conditions)
                if signal:
                    signals.append(signal)

        except Exception as e:
            print(f"生成信号失败 {stock_code}: {e}")

        return signals

    def _check_signal(
        self,
        row: pd.Series,
        df: pd.DataFrame,
        strategy: Strategy,
        stock_code: str,
        stock_name: str,
        conditions: dict,
    ) -> Optional[Signal]:
        """检查单行数据是否产生信号"""
        matched_conditions = []
        buy_score = 0
        sell_score = 0

        # 检查买入条件
        for condition in conditions.get("buy", []):
            if self._evaluate_condition(row, df, condition):
                matched_conditions.append({"condition": condition["text"], "type": "buy", "met": True})
                buy_score += 1
            else:
                matched_conditions.append({"condition": condition["text"], "type": "buy", "met": False})

        # 检查卖出条件
        for condition in conditions.get("sell", []):
            if self._evaluate_condition(row, df, condition):
                matched_conditions.append({"condition": condition["text"], "type": "sell", "met": True})
                sell_score += 1
            else:
                matched_conditions.append({"condition": condition["text"], "type": "sell", "met": False})

        # 计算置信度
        total_conditions = len(conditions.get("buy", [])) + len(conditions.get("sell", []))
        if total_conditions == 0:
            return None

        # 买入信号：买入条件满足率 > 70%
        buy_conditions = conditions.get("buy", [])
        if buy_conditions:
            buy_met = sum(1 for c in matched_conditions if c["type"] == "buy" and c["met"])
            buy_confidence = buy_met / len(buy_conditions)

            if buy_confidence >= 0.7:
                return Signal(
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    signal_type=SignalType.BUY,
                    signal_price=float(row["close"]),
                    signal_time=str(row["trade_date"]),
                    confidence=round(buy_confidence, 2),
                    matched_conditions=matched_conditions,
                )

        # 卖出信号：卖出条件满足率 > 70%
        sell_conditions = conditions.get("sell", [])
        if sell_conditions:
            sell_met = sum(1 for c in matched_conditions if c["type"] == "sell" and c["met"])
            sell_confidence = sell_met / len(sell_conditions)

            if sell_confidence >= 0.7:
                return Signal(
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    signal_type=SignalType.SELL,
                    signal_price=float(row["close"]),
                    signal_time=str(row["trade_date"]),
                    confidence=round(sell_confidence, 2),
                    matched_conditions=matched_conditions,
                )

        return None

    def _evaluate_condition(self, row: pd.Series, df: pd.DataFrame, condition: dict) -> bool:
        """评估单个条件"""
        text = condition["text"].lower()

        try:
            # MA条件
            ma_match = re.search(r"ma(\d+)\s*([<>]=?)\s*ma(\d+)", text)
            if ma_match:
                fast = f"ma{ma_match.group(1)}"
                op = ma_match.group(2)
                slow = f"ma{ma_match.group(3)}"
                if fast in row.index and slow in row.index:
                    if op == ">":
                        return bool(row[fast] > row[slow])
                    elif op == "<":
                        return bool(row[fast] < row[slow])

            # RSI条件
            rsi_match = re.search(r"rsi\s*([<>]=?)\s*(\d+)", text)
            if rsi_match:
                op = rsi_match.group(1)
                threshold = float(rsi_match.group(2))
                rsi_val = _safe_get(row, "rsi6", _safe_get(row, "rsi12", 50))
                if op == "<":
                    return bool(rsi_val < threshold)
                elif op == ">":
                    return bool(rsi_val > threshold)

            # 成交量条件
            if "放量" in text and "vol_ratio" in row.index:
                return bool(row["vol_ratio"] > 1.5)

            if "缩量" in text and "vol_ratio" in row.index:
                return bool(row["vol_ratio"] < 0.7)

            # 涨跌幅条件
            if "涨停" in text and "pct_change" in row.index:
                return bool(row["pct_change"] >= 9.8)

            if "跌停" in text and "pct_change" in row.index:
                return bool(row["pnl_pct"] <= -9.8)

            # MACD条件
            if "macd金叉" in text or "dif>dea" in text:
                if "dif" in row.index and "dea" in row.index:
                    return bool(row["dif"] > row["dea"])

            if "macd死叉" in text or "dif<dea" in text:
                if "dif" in row.index and "dea" in row.index:
                    return bool(row["dif"] < row["dea"])

            # 布林带条件
            if "触及布林上轨" in text or "boll上轨" in text:
                if "boll_upper" in row.index:
                    return bool(row["high"] >= row["boll_upper"])

            if "触及布林下轨" in text or "boll下轨" in text:
                if "boll_lower" in row.index:
                    return bool(row["low"] <= row["boll_lower"])

        except Exception:
            pass

        return False

    def scan_all_strategies(self, stock_codes: list[str] = None) -> list[Signal]:
        """扫描所有策略的信号"""
        all_signals = []

        # 获取所有策略
        strategies = self.repository.load_all()

        # 获取股票列表
        if not stock_codes:
            try:
                from deeppulse.agent.watchlist import get_watchlist

                watchlist = get_watchlist()
                stock_codes = [item["code"] for item in watchlist[:20]]
            except Exception:
                stock_codes = []

        # 扫描每个策略
        for strategy in strategies:
            for code in stock_codes:
                try:
                    signals = self.generate_signals(strategy, code)
                    for signal in signals:
                        # 保存信号
                        self.repository.save_signal(signal)
                        all_signals.append(signal)
                except Exception as e:
                    print(f"扫描失败 {strategy.name}/{code}: {e}")
                    continue

        return all_signals

    def get_latest_signals(self, limit: int = 50) -> list[Signal]:
        """获取最新信号"""
        return self.repository.get_signals(limit=limit)

    def get_strategy_signals(self, strategy_id: str, limit: int = 50) -> list[Signal]:
        """获取特定策略的信号"""
        return self.repository.get_signals(strategy_id=strategy_id, limit=limit)


# 全局实例
_generator = None


def get_signal_generator() -> SignalGenerator:
    """获取全局信号生成器实例"""
    global _generator
    if _generator is None:
        _generator = SignalGenerator()
    return _generator
