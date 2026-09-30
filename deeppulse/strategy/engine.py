"""策略引擎 - 策略解析、信号生成、条件匹配"""

import re
from datetime import date, timedelta
from typing import Any

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


class StrategyEngine:
    """策略引擎 - 核心策略处理逻辑"""

    def __init__(self):
        self.repository = get_repository()
        self._indicators_cache = {}

    def parse_conditions(self, content: str) -> dict:
        """从Markdown内容解析结构化条件"""
        conditions = {
            "buy": [],  # 买入条件
            "sell": [],  # 卖出条件
            "indicators": [],  # 使用的指标
            "keywords": [],  # 关键词
        }

        # 提取买入信号
        buy_section = re.search(r"##\s*买入信号\s*\n(.*?)(?=##|$)", content, re.DOTALL)
        if buy_section:
            buy_text = buy_section.group(1)
            conditions["buy"] = self._extract_conditions(buy_text)

        # 提取卖出信号
        sell_section = re.search(r"##\s*卖出信号\s*\n(.*?)(?=##|$)", content, re.DOTALL)
        if sell_section:
            sell_text = sell_section.group(1)
            conditions["sell"] = self._extract_conditions(sell_text)

        # 提取关键指标
        indicator_patterns = [
            (r"MA(\d+)", "MA"),
            (r"MACD", "MACD"),
            (r"RSI(\d+)", "RSI"),
            (r"KDJ", "KDJ"),
            (r"BOLL", "BOLL"),
            (r"ATR", "ATR"),
            (r"OBV", "OBV"),
        ]
        for pattern, name in indicator_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                conditions["indicators"].append(name)

        # 提取关键词
        keywords = set()
        tech_patterns = [
            r"金叉",
            r"死叉",
            r"超买",
            r"超卖",
            r"放量",
            r"缩量",
            r"突破",
            r"回踩",
            r"支撑",
            r"压力",
            r"背离",
            r"涨停",
            r"跌停",
            r"封板",
            r"换手",
        ]
        for pattern in tech_patterns:
            if re.search(pattern, content):
                keywords.add(pattern)
        conditions["keywords"] = list(keywords)

        return conditions

    def _extract_conditions(self, text: str) -> list[dict]:
        """从文本中提取条件"""
        conditions = []

        # 匹配条件列表项
        pattern = r"[-*]\s*(.+?)(?:\n|$)"
        matches = re.findall(pattern, text)

        for match in matches:
            condition = {
                "text": match.strip(),
                "type": self._classify_condition(match),
                "indicators": self._extract_indicators(match),
            }
            conditions.append(condition)

        return conditions

    def _classify_condition(self, text: str) -> str:
        """分类条件类型"""
        if any(kw in text for kw in ["买入", "建仓", "开仓", "做多"]):
            return "buy"
        elif any(kw in text for kw in ["卖出", "平仓", "止损", "止盈"]):
            return "sell"
        elif any(kw in text for kw in ["确认", "验证", "满足"]):
            return "confirm"
        return "general"

    def _extract_indicators(self, text: str) -> list[str]:
        """提取条件中涉及的指标"""
        indicators = []
        patterns = [
            (r"MA\d+", "MA"),
            (r"MACD", "MACD"),
            (r"RSI", "RSI"),
            (r"KDJ", "KDJ"),
            (r"BOLL", "BOLL"),
            (r"成交量", "VOLUME"),
            (r"换手率", "TURNOVER"),
        ]
        for pattern, name in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                indicators.append(name)
        return indicators

    def evaluate_strategy(self, strategy: Strategy, stock_code: str, days: int = 60) -> dict:
        """评估策略对某只股票的适用性"""
        from deeppulse.src.query import StockQuery

        query = StockQuery()
        end = date.today()
        start = end - timedelta(days=days * 3)

        # 获取K线数据
        df = query.get_daily_kline(stock_code, start_date=str(start), end_date=str(end))
        if df.empty:
            return {"error": f"股票 {stock_code} 无数据"}

        df = df.sort_values("trade_date").reset_index(drop=True)

        # 计算技术指标
        df = self._add_indicators(df)

        # 解析策略条件
        conditions = strategy.conditions if strategy.conditions else self.parse_conditions(strategy.content)

        # 评估条件匹配度
        match_result = self._evaluate_conditions(df, conditions)

        return {
            "stock_code": stock_code,
            "strategy_name": strategy.name,
            "match_score": match_result["score"],
            "matched_conditions": match_result["matched"],
            "current_indicators": match_result["indicators"],
            "recommendation": self._generate_recommendation(match_result),
        }

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加技术指标"""
        from deeppulse.agent.indicators import TechnicalIndicators

        return TechnicalIndicators.add_all(df)

    def evaluate_spec(self, spec, code: str, days: int = 250) -> dict:
        """评估 LLM 生成的策略规格（StrategySpec）对某只股票的最新触发情况

        返回最新 bar 的多因子得分明细、加权总分及是否触达入场阈值。
        """
        from deeppulse.src.query import StockQuery
        from deeppulse.strategy.spec import factor_scores_detail

        query = StockQuery()
        end = date.today()
        start = end - timedelta(days=days * 3)

        df = query.get_daily_kline(code, start_date=str(start), end_date=str(end))
        if df.empty:
            return {"error": f"股票 {code} 无数据"}

        df = df.sort_values("trade_date").reset_index(drop=True)
        df = self._add_indicators(df)

        detail = factor_scores_detail(spec, df)
        return {
            "stock_code": code,
            "strategy_name": spec.name,
            **detail,
        }

    def _evaluate_conditions(self, df: pd.DataFrame, conditions: dict) -> dict:
        """评估条件匹配度"""
        if df.empty:
            return {"score": 0, "matched": [], "indicators": {}}

        latest = df.iloc[-1]
        matched = []
        score = 0

        # 获取当前指标值
        indicators = {}
        indicator_cols = ["ma5", "ma10", "ma20", "ma60", "rsi6", "rsi12", "rsi24", "dif", "dea", "macd", "k", "d", "j"]
        for col in indicator_cols:
            if col in df.columns:
                indicators[col] = float(latest[col])

        # 评估买入条件
        for condition in conditions.get("buy", []):
            if self._check_condition(latest, condition, df):
                matched.append({"condition": condition["text"], "type": "buy", "met": True})
                score += 1
            else:
                matched.append({"condition": condition["text"], "type": "buy", "met": False})

        # 评估卖出条件
        for condition in conditions.get("sell", []):
            if self._check_condition(latest, condition, df):
                matched.append({"condition": condition["text"], "type": "sell", "met": True})
                score -= 1
            else:
                matched.append({"condition": condition["text"], "type": "sell", "met": False})

        # 计算总分（0-100）
        total_conditions = len(conditions.get("buy", [])) + len(conditions.get("sell", []))
        if total_conditions > 0:
            normalized_score = (score + total_conditions) / (2 * total_conditions) * 100
        else:
            normalized_score = 50

        return {
            "score": round(normalized_score, 2),
            "matched": matched,
            "indicators": indicators,
        }

    def _check_condition(self, row: pd.Series, condition: dict, df: pd.DataFrame) -> bool:
        """检查单个条件是否满足"""
        text = condition["text"].lower()

        try:
            # 检查MA相关条件
            ma_match = re.search(r"ma(\d+)\s*([<>]=?)\s*ma(\d+)", text)
            if ma_match:
                fast = f"ma{ma_match.group(1)}"
                op = ma_match.group(2)
                slow = f"ma{ma_match.group(3)}"
                if fast in row.index and slow in row.index:
                    if op == ">":
                        return row[fast] > row[slow]
                    elif op == "<":
                        return row[fast] < row[slow]

            # 检查RSI条件
            rsi_match = re.search(r"rsi\s*([<>]=?)\s*(\d+)", text)
            if rsi_match:
                op = rsi_match.group(1)
                threshold = float(rsi_match.group(2))
                rsi_val = _safe_get(row, "rsi6", _safe_get(row, "rsi12", 50))
                if op == "<":
                    return rsi_val < threshold
                elif op == ">":
                    return rsi_val > threshold

            # 检查成交量条件
            if "放量" in text and "vol_ratio" in row.index:
                return row["vol_ratio"] > 1.5

            if "缩量" in text and "vol_ratio" in row.index:
                return row["vol_ratio"] < 0.7

            # 检查涨跌幅条件
            if "涨停" in text and "pct_change" in row.index:
                return row["pct_change"] >= 9.8

            if "跌停" in text and "pct_change" in row.index:
                return row["pct_change"] <= -9.8

        except Exception:
            pass

        return False

    def _generate_recommendation(self, match_result: dict) -> str:
        """生成推荐建议"""
        score = match_result["score"]

        if score >= 80:
            return "强烈看多 - 多数买入条件满足，可考虑建仓"
        elif score >= 60:
            return "偏多 - 部分条件满足，可关注等待确认"
        elif score >= 40:
            return "中性 - 条件混合，建议观望"
        elif score >= 20:
            return "偏空 - 部分卖出条件满足，注意风险"
        else:
            return "强烈看空 - 多数卖出条件满足，建议回避"

    def scan_signals(self, strategy_ids: list[str] = None, stock_codes: list[str] = None) -> list[Signal]:
        """扫描信号"""
        signals = []

        # 获取策略列表
        if strategy_ids:
            strategies = [self.repository.get_strategy(sid) for sid in strategy_ids]
            strategies = [s for s in strategies if s]
        else:
            strategies = self.repository.load_all()

        # 获取股票列表
        if not stock_codes:
            stock_codes = self._get_default_stock_list()

        # 扫描每个策略对每只股票的信号
        for strategy in strategies:
            for code in stock_codes[:10]:  # 限制数量避免太慢
                try:
                    result = self.evaluate_strategy(strategy, code, days=30)
                    if "error" in result:
                        continue

                    # 根据匹配分数生成信号
                    score = result["match_score"]
                    if score >= 70:  # 买入信号阈值
                        signal = Signal(
                            strategy_id=strategy.id,
                            strategy_name=strategy.name,
                            stock_code=code,
                            stock_name=self._get_stock_name(code),
                            signal_type=SignalType.BUY,
                            signal_price=self._get_latest_price(code),
                            confidence=score / 100,
                            matched_conditions=result["matched_conditions"],
                        )
                        signals.append(signal)
                        self.repository.save_signal(signal)

                except Exception as e:
                    print(f"扫描信号失败 {strategy.name}/{code}: {e}")
                    continue

        return signals

    def _get_default_stock_list(self) -> list[str]:
        """获取默认股票列表（自选股）"""
        try:
            from deeppulse.agent.watchlist import get_watchlist

            watchlist = get_watchlist()
            return [item["code"] for item in watchlist[:20]]
        except Exception:
            return []

    def _get_stock_name(self, code: str) -> str:
        """获取股票名称"""
        try:
            from deeppulse.src.query import StockQuery

            query = StockQuery()
            info = query.get_stock_info(code)
            return info.get("name", code) if info else code
        except Exception:
            return code

    def _get_latest_price(self, code: str) -> float:
        """获取最新价格"""
        try:
            from deeppulse.src.realtime.manager import get_realtime_manager

            manager = get_realtime_manager()
            quote = manager.get_quote(code)
            return quote.get("price", 0) if quote else 0
        except Exception:
            return 0

    def backtest_strategy(
        self,
        strategy: Strategy,
        stock_code: str,
        days: int = 500,
        initial_capital: float = 100000,
        stop_loss: float = 0.05,
        take_profit: float = 0.10,
        max_hold_days: int = 20,
        **kwargs,
    ) -> dict:
        """回测策略"""
        from deeppulse.strategy.backtest import backtest_stock

        # 解析策略信号函数
        signal_func = self._get_signal_function(strategy)

        # 运行回测
        result = backtest_stock(
            code=stock_code,
            strategy=strategy.name,
            days=days,
            initial_capital=initial_capital,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_hold=max_hold_days,
            signal_func=signal_func,
            **kwargs,
        )

        return result

    def _get_signal_function(self, strategy: Strategy):
        """根据策略内容获取信号函数"""
        from deeppulse.strategy.backtest import (
            signal_ma_cross,
            signal_macd_cross,
            signal_rsi_oversold,
            signal_volume_breakout,
        )

        content = strategy.content.lower()

        # 根据策略内容选择信号函数
        if "macd" in content and ("金叉" in content or "死叉" in content):
            return signal_macd_cross
        elif "ma" in content and ("均线" in content or "金叉" in content):
            fast = 5
            slow = 10
            # 尝试从参数中提取
            fast_match = re.search(r"ma(\d+)", content)
            if fast_match:
                fast = int(fast_match.group(1))
            return lambda df: signal_ma_cross(df, fast=fast, slow=slow)
        elif "rsi" in content and ("超卖" in content or "超买" in content):
            return signal_rsi_oversold
        elif "放量" in content or "突破" in content:
            return signal_volume_breakout
        else:
            # 默认使用MA交叉
            return lambda df: signal_ma_cross(df, fast=5, slow=10)


# 全局实例
_engine = None


def get_engine() -> StrategyEngine:
    """获取全局策略引擎实例"""
    global _engine
    if _engine is None:
        _engine = StrategyEngine()
    return _engine
