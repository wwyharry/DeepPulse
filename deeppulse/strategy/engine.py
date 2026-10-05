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

        # 提取买入信号（允许标题后带冒号；### 子标题不截断正文）
        buy_section = re.search(
            r"^##[ \t]*买入信号[ \t]*[：:]?[ \t]*\r?$\n(.*?)(?=^##(?![#])|\Z)",
            content,
            re.DOTALL | re.MULTILINE,
        )
        if buy_section:
            buy_text = buy_section.group(1)
            conditions["buy"] = self._extract_conditions(buy_text)

        # 提取卖出信号
        sell_section = re.search(
            r"^##[ \t]*卖出信号[ \t]*[：:]?[ \t]*\r?$\n(.*?)(?=^##(?![#])|\Z)",
            content,
            re.DOTALL | re.MULTILINE,
        )
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

        # 匹配条件列表项（行首锚定：-/* 或数字编号 1. / 1) ，避免行中连字符被误判）
        pattern = r"^\s*(?:[-*]|\d+[.)])\s+(.+)$"
        matches = re.findall(pattern, text, flags=re.MULTILINE)

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
            ops = {
                ">": lambda a, b: a > b,
                ">=": lambda a, b: a >= b,
                "<": lambda a, b: a < b,
                "<=": lambda a, b: a <= b,
            }

            # 检查MA相关条件
            ma_match = re.search(r"ma(\d+)\s*([<>]=?)\s*ma(\d+)", text)
            if ma_match:
                fast = f"ma{ma_match.group(1)}"
                op = ma_match.group(2)
                slow = f"ma{ma_match.group(3)}"
                if fast in row.index and slow in row.index and op in ops:
                    return bool(ops[op](row[fast], row[slow]))

            # 检查RSI条件
            rsi_match = re.search(r"rsi\s*([<>]=?)\s*(\d+)", text)
            if rsi_match:
                op = rsi_match.group(1)
                threshold = float(rsi_match.group(2))
                rsi_val = _safe_get(row, "rsi6", _safe_get(row, "rsi12", 50))
                if op in ops:
                    return bool(ops[op](rsi_val, threshold))

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
        """扫描信号（同 strategy_id+stock_code+signal_date+signal_type 当日只入库一次）"""
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
            # 业务去重键 (stock_code, signal_type, signal_date)：signal_time 可能是
            # 完整时间戳或日期字符串，统一取前 10 位日期。同一天反复扫描不得
            # 以不同时间戳重复入库（唯一索引拦不住不同 signal_time）。
            # 最近信号按 signal_time 降序返回，limit 覆盖当日全部入库即可。
            saved_keys: set[tuple[str, str, str]] = set()
            for saved in self.repository.get_signals(strategy_id=strategy.id, limit=1000):
                saved_keys.add((saved.stock_code, str(saved.signal_type), str(saved.signal_time)[:10]))
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
                        dedup_key = (signal.stock_code, str(signal.signal_type), str(signal.signal_time)[:10])
                        if dedup_key in saved_keys:
                            continue  # 当日同键信号已入库，跳过不重复插入
                        signals.append(signal)
                        self.repository.save_signal(signal)
                        saved_keys.add(dedup_key)

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
            # get_stock_info 返回 DataFrame，不能做真值判断
            if info is not None and not info.empty:
                name = info.iloc[0].get("name")
                if name is not None and name == name and str(name).strip():
                    return str(name)
        except Exception:
            pass
        return code

    def _get_latest_price(self, code: str) -> float:
        """获取最新价格"""
        try:
            from deeppulse.agent.tools._shared import get_realtime_manager

            manager = get_realtime_manager()
            quote = manager.fetch_quote(code)  # 返回 RealtimeQuote（字段 .current）
            return float(quote.current or 0) if quote else 0
        except Exception:
            return 0

    def backtest_strategy(
        self,
        strategy: Strategy,
        stock_code: str,
        days: int = 500,
        initial_capital: float = 100000,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        max_hold_days: int | None = None,
        **kwargs,
    ) -> dict:
        """回测策略

        stop_loss/take_profit/max_hold_days 仅在静态退出引擎下生效。
        一旦显式传入任一参数且未显式要求动态退出，自动切换为静态引擎
        （use_dynamic_exit=False），保证参数切实生效；显式 use_dynamic_exit=True
        与静态参数混用时告警并忽略静态参数。
        """
        import warnings

        from deeppulse.strategy.backtest import backtest_stock

        # 解析策略信号函数
        signal_func = self._get_signal_function(strategy)

        static_requested = any(p is not None for p in (stop_loss, take_profit, max_hold_days))
        use_dynamic_exit = kwargs.pop("use_dynamic_exit", not static_requested)
        if static_requested and use_dynamic_exit:
            warnings.warn(
                "stop_loss/take_profit/max_hold_days 在动态退出下不生效；将忽略这些参数。"
                "如需固定止损请传 use_dynamic_exit=False。",
                UserWarning,
                stacklevel=2,
            )

        # 运行回测
        result = backtest_stock(
            code=stock_code,
            strategy=strategy.name,
            days=days,
            initial_capital=initial_capital,
            stop_loss=0.05 if stop_loss is None else stop_loss,
            take_profit=0.10 if take_profit is None else take_profit,
            max_hold=20 if max_hold_days is None else max_hold_days,
            signal_func=signal_func,
            use_dynamic_exit=use_dynamic_exit,
            **kwargs,
        )

        return result

    def _get_signal_function(self, strategy: Strategy):
        """根据策略内容获取信号函数"""
        import pandas as pd

        from deeppulse.strategy.backtest import (
            signal_macd_cross,
            signal_rsi_oversold,
            signal_volume_breakout,
        )

        content = strategy.content.lower()

        # 根据策略内容选择信号函数
        if "macd" in content and ("金叉" in content or "死叉" in content):
            return signal_macd_cross
        elif "ma" in content and ("均线" in content or "金叉" in content or "上穿" in content or "下穿" in content):
            # 提取全部均线周期并按大小配对快慢线（避免慢线被硬编码为 MA10）
            periods = sorted({int(x) for x in re.findall(r"ma(\d+)", content)})
            if len(periods) >= 2:
                fast, slow = periods[0], periods[-1]
            elif len(periods) == 1:
                # 单周期与默认慢线10配对，避免 fast==slow
                fast, slow = (periods[0], 10) if periods[0] < 10 else (10, periods[0])
            else:
                fast, slow = 5, 10
            if fast >= slow:
                fast, slow = 5, 10

            def _ma_cross(df, _f=fast, _s=slow):
                # 预置指标列仅含 ma5/10/20/60；其余周期（如 MA120）按需计算，避免 KeyError
                def _ma(p: int):
                    col = f"ma{p}"
                    return df[col] if col in df.columns else df["close"].rolling(p).mean()

                fast_ma, slow_ma = _ma(_f), _ma(_s)
                cross_up = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
                cross_down = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))
                signal = pd.Series(0, index=df.index)
                signal[cross_up] = 1
                signal[cross_down] = -1
                return signal

            return _ma_cross
        elif "rsi" in content and ("超卖" in content or "超买" in content):
            return signal_rsi_oversold
        elif "放量" in content or "突破" in content:
            return signal_volume_breakout
        else:
            # 默认使用MA交叉（MA5/MA10）

            def _ma_cross_default(df, _f=5, _s=10):
                def _ma(p: int):
                    col = f"ma{p}"
                    return df[col] if col in df.columns else df["close"].rolling(p).mean()

                fast_ma, slow_ma = _ma(_f), _ma(_s)
                cross_up = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
                cross_down = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))
                signal = pd.Series(0, index=df.index)
                signal[cross_up] = 1
                signal[cross_down] = -1
                return signal

            return _ma_cross_default


# 全局实例
_engine = None


def get_engine() -> StrategyEngine:
    """获取全局策略引擎实例"""
    global _engine
    if _engine is None:
        _engine = StrategyEngine()
    return _engine
