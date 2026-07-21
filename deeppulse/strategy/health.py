"""策略健康度监控 — 替代信号监控

每日扫描所有已保存策略，计算最新因子得分，保存快照。
不生成买卖信号，只展示数据供用户判断。
"""

import json
from datetime import date, datetime
from typing import Optional

import pandas as pd

from deeppulse.strategy.models import HealthSnapshot
from deeppulse.strategy.repository import get_repository


class HealthMonitor:
    """策略健康度监控器"""

    def __init__(self):
        self.repository = get_repository()

    def scan_all(self) -> list[HealthSnapshot]:
        """扫描所有已保存策略，计算最新因子得分，保存快照"""
        strategies = self.repository.list_stock_strategies()
        snapshots = []
        today = date.today().isoformat()

        for ss in strategies:
            try:
                snap = self._evaluate_strategy(ss, today)
                if snap:
                    self.repository.save_health_snapshot(snap)
                    snapshots.append(snap)
            except Exception as e:
                print(f"健康度扫描失败 {ss.stock_code}/{ss.name}: {e}")
                continue

        return snapshots

    def _evaluate_strategy(self, ss, snapshot_date: str) -> Optional[HealthSnapshot]:
        """评估单个策略的健康度"""
        from deeppulse.strategy.spec import factor_scores_detail

        df = self._load_kline(ss.stock_code, days=120)
        if df is None or df.empty:
            return None

        from deeppulse.agent.indicators import TechnicalIndicators
        df = TechnicalIndicators.add_all(df)

        spec = ss.get_spec()
        detail = factor_scores_detail(spec, df)

        # 最新价格和涨跌幅
        latest_close = float(df.iloc[-1]["close"])
        price_1d = 0.0
        price_5d = 0.0
        if len(df) >= 2:
            price_1d = (latest_close / float(df.iloc[-2]["close"]) - 1)
        if len(df) >= 6:
            price_5d = (latest_close / float(df.iloc[-6]["close"]) - 1)

        # 因子得分
        factor_list = []
        for f in detail.get("factors", []):
            factor_list.append({
                "factor_id": f["factor_id"],
                "factor_name": f["factor_name"],
                "score": round(f["score"], 4),
                "weighted_score": round(f["weighted_score"], 4),
            })

        return HealthSnapshot(
            strategy_id=ss.id,
            stock_code=ss.stock_code,
            stock_name=ss.stock_name,
            strategy_name=ss.name,
            total_score=round(detail.get("total_score", 0), 4),
            entry_threshold=detail.get("entry_threshold", 0.6),
            triggered=detail.get("triggered", False),
            factor_scores=json.dumps(factor_list, ensure_ascii=False),
            latest_close=round(latest_close, 2),
            price_change_1d=round(price_1d, 4),
            price_change_5d=round(price_5d, 4),
            snapshot_date=snapshot_date,
        )

    def get_dashboard(self) -> dict:
        """获取健康度面板数据"""
        snapshots = self.repository.get_latest_health()
        total = len(snapshots)
        healthy = sum(1 for s in snapshots if s.total_score >= s.entry_threshold)
        watch = sum(1 for s in snapshots if s.entry_threshold * 0.8 <= s.total_score < s.entry_threshold)
        abnormal = sum(1 for s in snapshots if s.total_score < s.entry_threshold * 0.8)

        return {
            "total": total,
            "healthy": healthy,
            "watch": watch,
            "abnormal": abnormal,
            "strategies": [s.to_dict() for s in snapshots],
        }

    def get_trend(self, strategy_id: str, days: int = 30) -> dict:
        """获取某策略的得分趋势"""
        snapshots = self.repository.get_health_trend(strategy_id, days)
        return {
            "strategy_id": strategy_id,
            "snapshots": [s.to_dict() for s in snapshots],
        }

    def _load_kline(self, stock_code: str, days: int = 120) -> Optional[pd.DataFrame]:
        """加载K线数据"""
        try:
            from datetime import timedelta
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


# 全局实例
_monitor = None


def get_health_monitor() -> HealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = HealthMonitor()
    return _monitor
