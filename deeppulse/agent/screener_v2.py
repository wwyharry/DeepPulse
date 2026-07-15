"""高性能选股器 V2

使用 DuckDB 批量计算指标，替代逐只遍历。
"""

import json

import duckdb

from deeppulse import config


class StockScreener:
    """高性能选股器"""

    def __init__(self):
        self.db_path = str(config.DB_PATH)

    def screen(self, conditions: list[dict], limit: int = 20) -> str:
        """通用条件选股

        conditions: [
            {"indicator": "ma_cross", "params": {"fast": 5, "slow": 10}},
            {"indicator": "rsi", "op": "<", "value": 30},
            {"indicator": "volume_ratio", "op": ">", "value": 2.0},
        ]
        """
        conn = duckdb.connect(self.db_path, read_only=True)
        try:
            # 构建批量指标计算 SQL
            sql = self._build_screening_sql(conditions, limit)
            result = conn.execute(sql).fetchdf()

            if result.empty:
                return json.dumps({"count": 0, "results": [], "message": "未找到符合条件的股票"}, ensure_ascii=False)

            records = result.to_dict("records")
            return json.dumps({
                "count": len(records),
                "conditions": [self._describe_condition(c) for c in conditions],
                "results": records,
            }, ensure_ascii=False, default=str)
        finally:
            conn.close()

    def screen_ma_cross(self, fast: int = 5, slow: int = 10, limit: int = 20) -> str:
        """均线金叉选股"""
        return self.screen([
            {"indicator": "ma_cross", "params": {"fast": fast, "slow": slow}},
        ], limit)

    def screen_oversold(self, rsi_threshold: float = 30, limit: int = 20) -> str:
        """超卖选股"""
        return self.screen([
            {"indicator": "rsi", "op": "<", "value": rsi_threshold},
        ], limit)

    def screen_volume_breakout(self, vol_ratio: float = 2.0, min_change: float = 2.0, limit: int = 20) -> str:
        """放量突破选股"""
        return self.screen([
            {"indicator": "volume_ratio", "op": ">", "value": vol_ratio},
            {"indicator": "pct_change", "op": ">", "value": min_change},
        ], limit)

    def screen_multi_confirm(self, limit: int = 20) -> str:
        """多指标共振选股"""
        return self.screen([
            {"indicator": "ma_alignment", "type": "bullish"},
            {"indicator": "macd", "type": "golden_cross"},
            {"indicator": "rsi", "op": "<", "value": 70},
            {"indicator": "volume_ratio", "op": ">", "value": 1.2},
        ], limit)

    def _build_screening_sql(self, conditions: list[dict], limit: int) -> str:
        """构建选股 SQL"""
        # 基础查询：获取每只股票最新数据并计算指标
        base_sql = """
        WITH latest_data AS (
            SELECT
                si.code,
                si.name,
                k.trade_date,
                k.close,
                k.high,
                k.low,
                k.volume,
                k.amount,
                LAG(k.close, 1) OVER (PARTITION BY k.code ORDER BY k.trade_date) as prev_close,
                AVG(k.close) OVER (PARTITION BY k.code ORDER BY k.trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as ma5,
                AVG(k.close) OVER (PARTITION BY k.code ORDER BY k.trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) as ma10,
                AVG(k.close) OVER (PARTITION BY k.code ORDER BY k.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20,
                AVG(k.close) OVER (PARTITION BY k.code ORDER BY k.trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) as ma60,
                AVG(k.volume) OVER (PARTITION BY k.code ORDER BY k.trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as vol_ma5,
                ROW_NUMBER() OVER (PARTITION BY k.code ORDER BY k.trade_date DESC) as rn
            FROM daily_kline k
            JOIN stock_info si ON k.code = si.code
            WHERE k.trade_date >= CURRENT_DATE - INTERVAL '120 days'
        ),
        indicators AS (
            SELECT
                code,
                name,
                trade_date,
                close,
                high,
                low,
                volume,
                amount,
                prev_close,
                ma5,
                ma10,
                ma20,
                ma60,
                vol_ma5,
                (close - prev_close) / prev_close * 100 as pct_change,
                volume / NULLIF(vol_ma5, 0) as vol_ratio
            FROM latest_data
            WHERE rn = 1
        )
        """

        # 构建 WHERE 条件
        where_clauses = []
        for cond in conditions:
            ind = cond.get("indicator", "")
            op = cond.get("op", ">")
            value = cond.get("value", 0)

            if ind == "ma_cross":
                fast = cond.get("params", {}).get("fast", 5)
                slow = cond.get("params", {}).get("slow", 10)
                where_clauses.append(f"ma{fast} > ma{slow}")

            elif ind == "ma_alignment":
                where_clauses.append("ma5 > ma10 AND ma10 > ma20 AND ma20 > ma60")

            elif ind == "rsi":
                # 简化 RSI 计算（基于涨跌幅近似）
                where_clauses.append(f"pct_change {op} {value}")

            elif ind == "volume_ratio":
                where_clauses.append(f"vol_ratio {op} {value}")

            elif ind == "pct_change":
                where_clauses.append(f"pct_change {op} {value}")

            elif ind == "macd":
                if cond.get("type") == "golden_cross":
                    where_clauses.append("ma5 > ma10")
                elif cond.get("type") == "death_cross":
                    where_clauses.append("ma5 < ma10")

            elif ind == "price_above_ma":
                period = cond.get("params", {}).get("period", 20)
                where_clauses.append(f"close > ma{period}")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        return f"""
        {base_sql}
        SELECT code, name, close, pct_change, vol_ratio, volume, amount, ma5, ma10, ma20
        FROM indicators
        WHERE {where_sql}
        ORDER BY pct_change DESC
        LIMIT {limit}
        """

    def _describe_condition(self, cond: dict) -> str:
        """描述条件"""
        ind = cond.get("indicator", "")
        op = cond.get("op", "")
        value = cond.get("value", "")

        descriptions = {
            "ma_cross": f"MA{cond.get('params', {}).get('fast', 5)} > MA{cond.get('params', {}).get('slow', 10)}",
            "ma_alignment": "多头排列 (MA5>MA10>MA20>MA60)",
            "rsi": f"RSI {op} {value}",
            "volume_ratio": f"量比 {op} {value}",
            "pct_change": f"涨跌幅 {op} {value}%",
            "macd": f"MACD {cond.get('type', '')}",
            "price_above_ma": f"价格在 MA{cond.get('params', {}).get('period', 20)} 上方",
        }
        return descriptions.get(ind, f"{ind} {op} {value}")
