"""绩效分析器 - 策略绩效分析和归因"""

import json
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from deeppulse.strategy.models import BacktestResult, PerformanceMetrics, Strategy
from deeppulse.strategy.repository import get_repository


class PerformanceAnalyzer:
    """绩效分析器"""

    def __init__(self):
        self.repository = get_repository()

    def analyze_strategy(self, strategy_id: str) -> dict:
        """分析策略绩效"""
        strategy = self.repository.get_strategy(strategy_id)
        if not strategy:
            return {"error": "策略不存在"}

        # 获取历史回测结果
        backtest_results = self._get_backtest_results(strategy_id)
        if not backtest_results:
            return {"error": "无回测数据"}

        # 计算综合指标
        metrics = self._compute_aggregate_metrics(backtest_results)

        # 计算各股票表现
        stock_performance = self._analyze_stock_performance(backtest_results)

        # 计算时间序列分析
        time_series = self._analyze_time_series(backtest_results)

        # 计算归因分析
        attribution = self._analyze_attribution(strategy, backtest_results)

        return {
            "strategy_id": strategy_id,
            "strategy_name": strategy.name,
            "metrics": metrics,
            "stock_performance": stock_performance,
            "time_series": time_series,
            "attribution": attribution,
            "recommendation": self._generate_recommendation(metrics),
        }

    def _get_backtest_results(self, strategy_id: str) -> list[dict]:
        """获取回测结果"""
        try:
            import duckdb
            from deeppulse import config

            if not config.DB_PATH.exists():
                return []

            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM backtest_results
                    WHERE strategy_id = ?
                    ORDER BY created_at DESC
                """,
                    [strategy_id],
                ).fetchall()

                columns = [desc[0] for desc in conn.description]
                return [dict(zip(columns, row)) for row in rows]
            finally:
                conn.close()
        except Exception:
            return []

    def _compute_aggregate_metrics(self, results: list[dict]) -> dict:
        """计算综合指标"""
        if not results:
            return {}

        returns = [r.get("total_return", 0) for r in results]
        win_rates = [r.get("win_rate", 0) for r in results]
        drawdowns = [r.get("max_drawdown", 0) for r in results]
        sharpe_ratios = [r.get("sharpe_ratio", 0) for r in results]

        return {
            "total_backtests": len(results),
            "avg_return": round(float(np.mean(returns)), 4),
            "median_return": round(float(np.median(returns)), 4),
            "std_return": round(float(np.std(returns)), 4),
            "avg_win_rate": round(float(np.mean(win_rates)), 4),
            "avg_max_drawdown": round(float(np.mean(drawdowns)), 4),
            "avg_sharpe_ratio": round(float(np.mean(sharpe_ratios)), 4),
            "best_return": round(float(max(returns)), 4),
            "worst_return": round(float(min(returns)), 4),
            "positive_rate": round(sum(1 for r in returns if r > 0) / len(returns), 4),
            "consistency": round(1 - float(np.std(returns)) / (abs(float(np.mean(returns))) + 0.001), 4),
        }

    def _analyze_stock_performance(self, results: list[dict]) -> list[dict]:
        """分析各股票表现"""
        stock_data = {}

        for r in results:
            code = r.get("stock_code", "")
            if code not in stock_data:
                stock_data[code] = {
                    "code": code,
                    "name": r.get("stock_name", ""),
                    "backtest_count": 0,
                    "total_return": 0,
                    "win_rate": 0,
                    "max_drawdown": 0,
                }

            stock_data[code]["backtest_count"] += 1
            stock_data[code]["total_return"] += r.get("total_return", 0)
            stock_data[code]["win_rate"] += r.get("win_rate", 0)
            stock_data[code]["max_drawdown"] = min(
                stock_data[code]["max_drawdown"], r.get("max_drawdown", 0)
            )

        # 计算平均值
        for code, data in stock_data.items():
            count = data["backtest_count"]
            if count > 0:
                data["total_return"] = round(data["total_return"] / count, 4)
                data["win_rate"] = round(data["win_rate"] / count, 4)

        # 按收益率排序
        sorted_stocks = sorted(stock_data.values(), key=lambda x: x["total_return"], reverse=True)

        return sorted_stocks[:20]  # 返回前20只

    def _analyze_time_series(self, results: list[dict]) -> dict:
        """时间序列分析"""
        if not results:
            return {}

        # 按时间排序
        sorted_results = sorted(results, key=lambda x: x.get("created_at", ""))

        # 提取时间序列数据
        dates = []
        returns = []
        for r in sorted_results:
            created_at = r.get("created_at", "")
            if created_at:
                dates.append(created_at[:10])  # 只取日期部分
                returns.append(r.get("total_return", 0))

        if not dates:
            return {}

        # 计算移动平均
        window = min(5, len(returns))
        if window > 0:
            ma_returns = np.convolve(returns, np.ones(window) / window, mode="valid")
            ma_returns = ma_returns.tolist()
        else:
            ma_returns = returns

        # 计算趋势
        if len(returns) >= 2:
            trend = "上升" if returns[-1] > returns[0] else "下降"
            trend_strength = abs(returns[-1] - returns[0]) / (abs(returns[0]) + 0.001)
        else:
            trend = "平稳"
            trend_strength = 0

        return {
            "dates": dates,
            "returns": returns,
            "ma_returns": ma_returns,
            "trend": trend,
            "trend_strength": round(trend_strength, 4),
            "volatility": round(float(np.std(returns)), 4) if len(returns) > 1 else 0,
        }

    def _analyze_attribution(self, strategy: Strategy, results: list[dict]) -> dict:
        """归因分析"""
        attribution = {
            "factors": [],
            "insights": [],
        }

        if not results:
            return attribution

        # 分析哪些条件贡献收益
        conditions = strategy.conditions or {}
        keywords = conditions.get("keywords", [])

        # 统计各关键词的出现频率和对应收益
        keyword_returns = {}
        for r in results:
            trades = r.get("trades", [])
            for trade in trades:
                signal = trade.get("signal", "")
                pnl = trade.get("pnl_pct", 0)

                for keyword in keywords:
                    if keyword in signal:
                        if keyword not in keyword_returns:
                            keyword_returns[keyword] = []
                        keyword_returns[keyword].append(pnl)

        # 计算各因子的贡献
        for keyword, returns in keyword_returns.items():
            avg_return = np.mean(returns)
            attribution["factors"].append({
                "factor": keyword,
                "avg_return": round(float(avg_return), 4),
                "count": len(returns),
                "contribution": round(float(avg_return * len(returns)), 4),
            })

        # 排序
        attribution["factors"].sort(key=lambda x: x["contribution"], reverse=True)

        # 生成洞察
        if attribution["factors"]:
            best_factor = attribution["factors"][0]
            worst_factor = attribution["factors"][-1]

            attribution["insights"].append(
                f"表现最好的因子是 '{best_factor['factor']}'，平均收益 {best_factor['avg_return']:.2%}"
            )

            if worst_factor["avg_return"] < 0:
                attribution["insights"].append(
                    f"表现最差的因子是 '{worst_factor['factor']}'，平均收益 {worst_factor['avg_return']:.2%}，建议优化或移除"
                )

        return attribution

    def _generate_recommendation(self, metrics: dict) -> str:
        """生成策略推荐"""
        if not metrics:
            return "数据不足，无法生成推荐"

        avg_return = metrics.get("avg_return", 0)
        avg_win_rate = metrics.get("avg_win_rate", 0)
        avg_sharpe = metrics.get("avg_sharpe_ratio", 0)
        positive_rate = metrics.get("positive_rate", 0)

        # 评分
        score = 0
        if avg_return > 0.1:
            score += 3
        elif avg_return > 0.05:
            score += 2
        elif avg_return > 0:
            score += 1

        if avg_win_rate > 0.6:
            score += 2
        elif avg_win_rate > 0.5:
            score += 1

        if avg_sharpe > 1:
            score += 2
        elif avg_sharpe > 0.5:
            score += 1

        if positive_rate > 0.7:
            score += 1

        # 生成推荐
        if score >= 7:
            return "强烈推荐 - 策略表现优秀，收益稳定，风险可控"
        elif score >= 5:
            return "推荐 - 策略表现良好，建议适当使用"
        elif score >= 3:
            return "中性 - 策略表现一般，需要进一步优化"
        elif score >= 1:
            return "谨慎 - 策略表现不佳，建议谨慎使用或重新设计"
        else:
            return "不推荐 - 策略表现差，建议重新设计"

    def compare_strategies(self, strategy_ids: list[str]) -> dict:
        """比较多个策略"""
        comparisons = []

        for strategy_id in strategy_ids:
            analysis = self.analyze_strategy(strategy_id)
            if "error" not in analysis:
                comparisons.append({
                    "strategy_id": strategy_id,
                    "strategy_name": analysis.get("strategy_name", ""),
                    "metrics": analysis.get("metrics", {}),
                    "recommendation": analysis.get("recommendation", ""),
                })

        if not comparisons:
            return {"error": "无可比较的策略"}

        # 按平均收益排序
        comparisons.sort(
            key=lambda x: x["metrics"].get("avg_return", 0),
            reverse=True,
        )

        # 添加排名
        for i, comp in enumerate(comparisons):
            comp["rank"] = i + 1

        return {
            "comparisons": comparisons,
            "best_strategy": comparisons[0] if comparisons else None,
            "worst_strategy": comparisons[-1] if comparisons else None,
        }

    def get_performance_summary(self) -> dict:
        """获取所有策略的绩效摘要"""
        try:
            import duckdb
            from deeppulse import config

            if not config.DB_PATH.exists():
                return {}

            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                # 策略总数
                total_strategies = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]

                # 活跃策略数
                active_strategies = conn.execute(
                    "SELECT COUNT(*) FROM strategies WHERE status = 'active'"
                ).fetchone()[0]

                # 回测总数
                total_backtests = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]

                # 平均收益
                avg_return = conn.execute(
                    "SELECT AVG(total_return) FROM backtest_results"
                ).fetchone()[0] or 0

                # 信号总数
                total_signals = conn.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]

                return {
                    "total_strategies": total_strategies,
                    "active_strategies": active_strategies,
                    "total_backtests": total_backtests,
                    "avg_return": round(float(avg_return), 4),
                    "total_signals": total_signals,
                }
            finally:
                conn.close()
        except Exception:
            return {}


# 全局实例
_analyzer = None


def get_performance_analyzer() -> PerformanceAnalyzer:
    """获取全局绩效分析器实例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = PerformanceAnalyzer()
    return _analyzer
