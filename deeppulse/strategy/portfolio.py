"""策略组合管理 - 多策略组合、资金分配、风险预算"""

import json

import numpy as np

from deeppulse.strategy.models import Portfolio
from deeppulse.strategy.repository import get_repository


class PortfolioManager:
    """组合管理器"""

    def __init__(self):
        self.repository = get_repository()

    def create_portfolio(
        self,
        name: str,
        description: str = "",
        strategies: list = None,
        total_capital: float = 1000000,
        risk_budget: float = 0.02,
        rebalance_frequency: str = "monthly",
    ) -> Portfolio:
        """创建组合"""
        portfolio = Portfolio(
            name=name,
            description=description,
            strategies=strategies or [],
            total_capital=total_capital,
            risk_budget=risk_budget,
            rebalance_frequency=rebalance_frequency,
        )

        self._save_portfolio(portfolio)
        return portfolio

    def get_portfolio(self, portfolio_id: str) -> Portfolio | None:
        """获取组合"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return None

            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                row = conn.execute(
                    "SELECT * FROM portfolios WHERE id = ?",
                    [portfolio_id],
                ).fetchone()

                if not row:
                    return None

                columns = [desc[0] for desc in conn.description]
                data = dict(zip(columns, row, strict=True))

                # 解析JSON字段
                if "strategies" in data and isinstance(data["strategies"], str):
                    data["strategies"] = json.loads(data["strategies"])

                return Portfolio(**data)
            finally:
                conn.close()
        except Exception:
            return None

    def list_portfolios(self) -> list[dict]:
        """列出所有组合"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return []

            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                rows = conn.execute("SELECT * FROM portfolios ORDER BY updated_at DESC").fetchall()

                columns = [desc[0] for desc in conn.description]
                portfolios = []
                for row in rows:
                    data = dict(zip(columns, row, strict=True))
                    if "strategies" in data and isinstance(data["strategies"], str):
                        data["strategies"] = json.loads(data["strategies"])
                    portfolios.append(data)

                return portfolios
            finally:
                conn.close()
        except Exception:
            return []

    def update_portfolio(self, portfolio_id: str, **kwargs) -> Portfolio | None:
        """更新组合"""
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return None

        # 更新字段
        for key, value in kwargs.items():
            if hasattr(portfolio, key):
                setattr(portfolio, key, value)

        self._save_portfolio(portfolio)
        return portfolio

    def delete_portfolio(self, portfolio_id: str) -> bool:
        """删除组合"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return False

            conn = duckdb.connect(str(config.DB_PATH))
            try:
                conn.execute("DELETE FROM portfolios WHERE id = ?", [portfolio_id])
                return True
            finally:
                conn.close()
        except Exception:
            return False

    def _save_portfolio(self, portfolio: Portfolio):
        """保存组合"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return

            conn = duckdb.connect(str(config.DB_PATH))
            try:
                # 创建表（如果不存在）
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS portfolios (
                        id VARCHAR PRIMARY KEY,
                        name VARCHAR,
                        description TEXT,
                        strategies VARCHAR,
                        total_capital DOUBLE,
                        risk_budget DOUBLE,
                        rebalance_frequency VARCHAR,
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                """)

                # 插入或更新
                conn.execute(
                    """
                    INSERT OR REPLACE INTO portfolios
                    (id, name, description, strategies, total_capital, risk_budget,
                     rebalance_frequency, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        portfolio.id,
                        portfolio.name,
                        portfolio.description,
                        json.dumps(portfolio.strategies, ensure_ascii=False),
                        portfolio.total_capital,
                        portfolio.risk_budget,
                        portfolio.rebalance_frequency,
                        portfolio.created_at,
                        portfolio.updated_at,
                    ],
                )
            finally:
                conn.close()
        except Exception:
            pass

    def add_strategy_to_portfolio(
        self,
        portfolio_id: str,
        strategy_id: str,
        weight: float = 0.1,
        params: dict = None,
    ) -> bool:
        """添加策略到组合"""
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return False

        # 检查是否已存在
        for s in portfolio.strategies:
            if s.get("strategy_id") == strategy_id:
                return False

        # 添加策略
        portfolio.strategies.append(
            {
                "strategy_id": strategy_id,
                "weight": weight,
                "params": params or {},
            }
        )

        # 重新平衡权重
        self._rebalance_weights(portfolio)

        self._save_portfolio(portfolio)
        return True

    def remove_strategy_from_portfolio(self, portfolio_id: str, strategy_id: str) -> bool:
        """从组合移除策略"""
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return False

        # 移除策略
        portfolio.strategies = [s for s in portfolio.strategies if s.get("strategy_id") != strategy_id]

        # 重新平衡权重
        self._rebalance_weights(portfolio)

        self._save_portfolio(portfolio)
        return True

    def _rebalance_weights(self, portfolio: Portfolio):
        """重新平衡权重"""
        if not portfolio.strategies:
            return

        # 平均分配权重
        total_weight = sum(s.get("weight", 0) for s in portfolio.strategies)
        if total_weight > 0:
            for s in portfolio.strategies:
                s["weight"] = s.get("weight", 0) / total_weight

    def optimize_portfolio(self, portfolio_id: str) -> dict:
        """优化组合配置"""
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return {"error": "组合不存在"}

        # 获取每个策略的绩效数据
        strategy_performance = []
        for s in portfolio.strategies:
            strategy_id = s.get("strategy_id")
            analysis = self._get_strategy_performance(strategy_id)
            if analysis:
                strategy_performance.append(
                    {
                        "strategy_id": strategy_id,
                        "weight": s.get("weight", 0),
                        "performance": analysis,
                    }
                )

        if not strategy_performance:
            return {"error": "无策略绩效数据"}

        # 计算最优权重（简化版：按夏普比率加权）
        total_sharpe = sum(sp["performance"].get("sharpe_ratio", 0) for sp in strategy_performance)

        optimized_weights = []
        for sp in strategy_performance:
            sharpe = sp["performance"].get("sharpe_ratio", 0)
            optimal_weight = sharpe / total_sharpe if total_sharpe > 0 else 1 / len(strategy_performance)
            optimized_weights.append(
                {
                    "strategy_id": sp["strategy_id"],
                    "current_weight": sp["weight"],
                    "optimal_weight": round(optimal_weight, 4),
                    "sharpe_ratio": sharpe,
                }
            )

        return {
            "portfolio_id": portfolio_id,
            "current_weights": optimized_weights,
            "optimization_suggestion": "建议按夏普比率调整权重以优化风险收益比",
        }

    def _get_strategy_performance(self, strategy_id: str) -> dict:
        """获取策略绩效"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return {}

            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                rows = conn.execute(
                    """
                    SELECT total_return, win_rate, sharpe_ratio, max_drawdown
                    FROM backtest_results
                    WHERE strategy_id = ?
                """,
                    [strategy_id],
                ).fetchall()

                if not rows:
                    return {}

                returns = [r[0] for r in rows]
                win_rates = [r[1] for r in rows]
                sharpes = [r[2] for r in rows]
                drawdowns = [r[3] for r in rows]

                return {
                    "avg_return": round(float(np.mean(returns)), 4),
                    "avg_win_rate": round(float(np.mean(win_rates)), 4),
                    "sharpe_ratio": round(float(np.mean(sharpes)), 4),
                    "avg_max_drawdown": round(float(np.mean(drawdowns)), 4),
                    "backtest_count": len(rows),
                }
            finally:
                conn.close()
        except Exception:
            return {}

    def get_portfolio_performance(self, portfolio_id: str) -> dict:
        """获取组合绩效"""
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return {"error": "组合不存在"}

        # 获取每个策略的绩效
        strategy_performances = []
        total_weighted_return = 0
        total_weighted_risk = 0

        for s in portfolio.strategies:
            strategy_id = s.get("strategy_id")
            weight = s.get("weight", 0)
            performance = self._get_strategy_performance(strategy_id)

            if performance:
                avg_return = performance.get("avg_return", 0)
                avg_drawdown = performance.get("avg_max_drawdown", 0)

                total_weighted_return += weight * avg_return
                total_weighted_risk += weight * abs(avg_drawdown)

                strategy_performances.append(
                    {
                        "strategy_id": strategy_id,
                        "weight": weight,
                        "performance": performance,
                    }
                )

        # 计算组合指标
        portfolio_metrics = {
            "expected_return": round(total_weighted_return, 4),
            "expected_risk": round(total_weighted_risk, 4),
            "risk_adjusted_return": round(total_weighted_return / (total_weighted_risk + 0.001), 4),
            "diversification_score": round(1 - sum(s.get("weight", 0) ** 2 for s in portfolio.strategies), 4),
        }

        return {
            "portfolio_id": portfolio_id,
            "portfolio_name": portfolio.name,
            "total_capital": portfolio.total_capital,
            "risk_budget": portfolio.risk_budget,
            "strategy_count": len(portfolio.strategies),
            "strategy_performances": strategy_performances,
            "portfolio_metrics": portfolio_metrics,
        }

    def calculate_position_sizes(self, portfolio_id: str) -> dict:
        """计算各策略的仓位大小"""
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return {"error": "组合不存在"}

        positions = []
        for s in portfolio.strategies:
            strategy_id = s.get("strategy_id")
            weight = s.get("weight", 0)
            allocated_capital = portfolio.total_capital * weight

            positions.append(
                {
                    "strategy_id": strategy_id,
                    "weight": weight,
                    "allocated_capital": round(allocated_capital, 2),
                    "risk_budget": round(allocated_capital * portfolio.risk_budget, 2),
                }
            )

        return {
            "portfolio_id": portfolio_id,
            "total_capital": portfolio.total_capital,
            "positions": positions,
        }


# 全局实例
_manager = None


def get_portfolio_manager() -> PortfolioManager:
    """获取全局组合管理器实例"""
    global _manager
    if _manager is None:
        _manager = PortfolioManager()
    return _manager
