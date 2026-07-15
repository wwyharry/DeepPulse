"""预测跟踪与结果反馈"""

import json
import uuid
from datetime import datetime, timedelta

from deeppulse import config
from deeppulse.src.database import get_connection


class PredictionTracker:
    """预测跟踪与结果反馈"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    def _conn(self):
        return get_connection(self.db_path)

    def save_prediction(
        self,
        stock_code: str,
        stock_name: str = "",
        prediction_type: str = "direction",
        direction: str = "neutral",
        target_price: float = 0,
        stop_loss: float = 0,
        timeframe_days: int = 5,
        reasoning: str = "",
        confidence: float = 0.5,
        memory_ids: list[str] = None,
    ) -> str:
        """保存一条预测"""
        pred_id = str(uuid.uuid4())
        now = datetime.now()
        check_date = now + timedelta(days=timeframe_days)

        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO predictions
                (id, stock_code, stock_name, prediction_type, direction,
                 target_price, stop_loss, timeframe_days, reasoning, confidence,
                 memory_ids_json, created_at, check_after_date, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
                [
                    pred_id,
                    stock_code,
                    stock_name,
                    prediction_type,
                    direction,
                    target_price,
                    stop_loss,
                    timeframe_days,
                    reasoning,
                    confidence,
                    json.dumps(memory_ids or [], ensure_ascii=False),
                    now,
                    check_date,
                ],
            )
            return json.dumps(
                {
                    "status": "saved",
                    "prediction_id": pred_id,
                    "stock_code": stock_code,
                    "direction": direction,
                    "check_after": str(check_date.date()),
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()

    def check_predictions(self, stock_code: str = None, current_price: float = None) -> str:
        """检查预测结果。提供 stock_code 时检查该股票的预测，否则检查所有到期预测。"""
        conn = self._conn()
        try:
            if stock_code:
                rows = conn.execute(
                    """
                    SELECT id, stock_code, stock_name, direction, target_price,
                           stop_loss, timeframe_days, reasoning, confidence,
                           memory_ids_json, created_at, outcome
                    FROM predictions
                    WHERE stock_code = ? AND outcome = 'pending'
                    ORDER BY created_at DESC
                """,
                    [stock_code],
                ).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, stock_code, stock_name, direction, target_price,
                           stop_loss, timeframe_days, reasoning, confidence,
                           memory_ids_json, created_at, outcome
                    FROM predictions
                    WHERE outcome = 'pending' AND check_after_date <= current_date
                    ORDER BY created_at DESC
                    LIMIT 20
                """).fetchall()

            if not rows:
                return json.dumps({"message": "没有待验证的预测", "checked": 0}, ensure_ascii=False)

            results = []
            for row in rows:
                pred = {
                    "id": row[0],
                    "stock_code": row[1],
                    "stock_name": row[2],
                    "direction": row[3],
                    "target_price": row[4],
                    "stop_loss": row[5],
                    "timeframe_days": row[6],
                    "reasoning": row[7][:100] if row[7] else "",
                    "confidence": row[8],
                    "created_at": str(row[10]),
                    "days_elapsed": (datetime.now() - row[10]).days,
                }
                results.append(pred)

            return json.dumps(
                {
                    "pending_predictions": results,
                    "total": len(results),
                    "message": f"找到 {len(results)} 个待验证预测，请用 verify_prediction 验证结果",
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()

    def verify_prediction(self, prediction_id: str, actual_price: float, actual_return_pct: float = None) -> str:
        """验证单个预测结果"""
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT direction, target_price, stop_loss, created_at, stock_code
                FROM predictions WHERE id = ?
            """,
                [prediction_id],
            ).fetchone()

            if not row:
                return json.dumps({"error": f"预测 {prediction_id} 不存在"}, ensure_ascii=False)

            direction, target_price, stop_loss, created_at, stock_code = row
            days_held = (datetime.now() - created_at).days

            # 判断结果
            if actual_return_pct is None and target_price and target_price > 0:
                # 用目标价估算收益
                actual_return_pct = 0  # 需要外部提供

            if direction == "bullish":
                if actual_return_pct and actual_return_pct > 3:
                    outcome = "correct"
                elif actual_return_pct and actual_return_pct < -3:
                    outcome = "wrong"
                else:
                    outcome = "partial"
            elif direction == "bearish":
                if actual_return_pct and actual_return_pct < -3:
                    outcome = "correct"
                elif actual_return_pct and actual_return_pct > 3:
                    outcome = "wrong"
                else:
                    outcome = "partial"
            else:
                outcome = "partial"

            now = datetime.now()
            conn.execute(
                """
                UPDATE predictions
                SET actual_price = ?, actual_return_pct = ?, outcome = ?,
                    checked_at = ?
                WHERE id = ?
            """,
                [actual_price, actual_return_pct, outcome, now, prediction_id],
            )

            # 更新关联记忆的重要性
            memory_ids_json = conn.execute(
                "SELECT memory_ids_json FROM predictions WHERE id = ?", [prediction_id]
            ).fetchone()[0]
            memory_ids = json.loads(memory_ids_json) if memory_ids_json else []

            if memory_ids and outcome in ("correct", "wrong"):
                adjustment = 0.1 if outcome == "correct" else -0.1
                for mid in memory_ids:
                    conn.execute(
                        """
                        UPDATE long_term_memories
                        SET importance = MIN(1.0, MAX(0.0, importance + ?)),
                            updated_at = ?
                        WHERE id = ?
                    """,
                        [adjustment, now, mid],
                    )

            return json.dumps(
                {
                    "prediction_id": prediction_id,
                    "stock_code": stock_code,
                    "direction": direction,
                    "actual_return_pct": actual_return_pct,
                    "outcome": outcome,
                    "days_held": days_held,
                    "memory_adjusted": len(memory_ids),
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()

    def get_accuracy_stats(self) -> str:
        """获取预测准确率统计"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT outcome, COUNT(*)
                FROM predictions
                WHERE outcome != 'pending'
                GROUP BY outcome
            """).fetchall()

            stats = {row[0]: row[1] for row in rows}
            total = sum(stats.values())
            correct = stats.get("correct", 0)
            wrong = stats.get("wrong", 0)
            partial = stats.get("partial", 0)

            # 按方向统计
            dir_rows = conn.execute("""
                SELECT direction, outcome, COUNT(*)
                FROM predictions
                WHERE outcome != 'pending'
                GROUP BY direction, outcome
            """).fetchall()

            by_direction = {}
            for d, o, c in dir_rows:
                if d not in by_direction:
                    by_direction[d] = {}
                by_direction[d][o] = c

            return json.dumps(
                {
                    "total_verified": total,
                    "correct": correct,
                    "wrong": wrong,
                    "partial": partial,
                    "accuracy": f"{correct / total * 100:.1f}%" if total > 0 else "N/A",
                    "by_direction": by_direction,
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()
