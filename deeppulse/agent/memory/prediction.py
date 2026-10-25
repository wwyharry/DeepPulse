"""预测跟踪与结果反馈"""

import json
import uuid
from datetime import datetime, timedelta

from deeppulse import config
from deeppulse.src.database import get_connection

# 预测方向白名单（模型随口写的方向词不得入库污染准确率）
VALID_DIRECTIONS = ("bullish", "bearish", "neutral")

# 收益率判定带：|收益| > 3% 记正确/错误，区间内记部分（工具描述如实披露）
RETURN_BAND_PCT = 3.0

# 仅目标价无止损时，反向偏离目标超 15% 记错误（否则暴跌也永远 partial，无法证伪）
ADVERSE_BAND = 0.15

# pending 超期宽限天数：过了宽限仍未验证 → expired（不伪造成 partial）
EXPIRE_GRACE_DAYS = 30


def _clean_price(value):
    """价格清洗：None/0/负数 = 未设置（未知≠0，存 NULL 不伪 0）"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


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
        target_price: float = None,
        stop_loss: float = None,
        timeframe_days: int = 5,
        reasoning: str = "",
        confidence: float = 0.5,
        memory_ids: list[str] = None,
        entry_price: float = None,
    ) -> str:
        """保存一条预测（入参全量校验，落库数据必须可信）"""
        # 方向白名单
        direction = (direction or "").strip().lower()
        if direction not in VALID_DIRECTIONS:
            return json.dumps(
                {"error": f"direction 非法: {direction!r}，只允许 {list(VALID_DIRECTIONS)}"}, ensure_ascii=False
            )

        # 时间窗
        try:
            timeframe_days = int(timeframe_days)
        except (TypeError, ValueError):
            return json.dumps({"error": f"timeframe_days 必须为整数，收到 {timeframe_days!r}"}, ensure_ascii=False)
        if not (1 <= timeframe_days <= 365):
            return json.dumps({"error": f"timeframe_days 必须在 1-365 之间，收到 {timeframe_days}"}, ensure_ascii=False)

        # 置信度
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return json.dumps({"error": f"confidence 必须为数字，收到 {confidence!r}"}, ensure_ascii=False)
        if not (0.0 <= confidence <= 1.0):
            return json.dumps({"error": f"confidence 必须在 0.0-1.0 之间，收到 {confidence}"}, ensure_ascii=False)

        # 价格（未设置 = None；0/负数不落库冒充价格）
        # 0 = 未设置（兼容旧调用），存 NULL；负数/非数才是非法输入
        target = _clean_price(target_price)
        stop = _clean_price(stop_loss)
        entry = _clean_price(entry_price)
        for name, raw, cleaned in (
            ("target_price", target_price, target),
            ("stop_loss", stop_loss, stop),
            ("entry_price", entry_price, entry),
        ):
            if raw is None or cleaned is not None:
                continue
            try:
                if float(raw) == 0:
                    continue  # 0 = 未设置哨兵
            except (TypeError, ValueError):
                pass
            return json.dumps({"error": f"{name} 必须为正数（未设置请传 null/0）"}, ensure_ascii=False)
        if direction == "bullish" and target and stop and target <= stop:
            return json.dumps({"error": "bullish 预测要求 target_price > stop_loss"}, ensure_ascii=False)
        if direction == "bearish" and target and stop and target >= stop:
            return json.dumps({"error": "bearish 预测要求 target_price < stop_loss"}, ensure_ascii=False)

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
                    target,
                    stop,
                    timeframe_days,
                    reasoning,
                    confidence,
                    json.dumps(memory_ids or [], ensure_ascii=False),
                    now,
                    check_date,
                ],
            )
            # entry_price 落 outcome_notes 旁注？不——列不存在时走 created 旁路：
            # predictions 无 entry_price 列，存进 reasoning 头部会污染展示；
            # 用单独的 ALTER 增列（幂等）。
            if entry is not None:
                try:
                    conn.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS entry_price DOUBLE")
                    conn.execute("UPDATE predictions SET entry_price = ? WHERE id = ?", [entry, pred_id])
                except Exception:
                    pass
            return json.dumps(
                {
                    "status": "saved",
                    "prediction_id": pred_id,
                    "stock_code": stock_code,
                    "direction": direction,
                    "target_price": target,
                    "stop_loss": stop,
                    "entry_price": entry,
                    "confidence": confidence,
                    "check_after": str(check_date.date()),
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()

    def _expire_overdue(self, conn) -> int:
        """超宽限期的 pending 自动结转 expired（不伪造成 partial/unknown 结果）"""
        cutoff = datetime.now() - timedelta(days=EXPIRE_GRACE_DAYS)
        cur = conn.execute(
            """
            UPDATE predictions
            SET outcome = 'expired', checked_at = ?,
                outcome_notes = COALESCE(outcome_notes, '') || '超过验证宽限期自动结转为 expired'
            WHERE outcome = 'pending' AND check_after_date < ?
        """,
            [datetime.now(), cutoff.date()],
        )
        n = cur.fetchone()
        return n[0] if n else 0

    def check_predictions(self, stock_code: str = None) -> str:
        """检查预测结果。提供 stock_code 时检查该股票的预测，否则检查所有到期预测。"""
        conn = self._conn()
        try:
            self._expire_overdue(conn)
            today = datetime.now().date()

            if stock_code:
                rows = conn.execute(
                    """
                    SELECT id, stock_code, stock_name, direction, target_price,
                           stop_loss, timeframe_days, reasoning, confidence,
                           memory_ids_json, created_at, outcome, check_after_date
                    FROM predictions
                    WHERE stock_code = ? AND outcome = 'pending'
                    ORDER BY check_after_date ASC
                """,
                    [stock_code],
                ).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, stock_code, stock_name, direction, target_price,
                           stop_loss, timeframe_days, reasoning, confidence,
                           memory_ids_json, created_at, outcome, check_after_date
                    FROM predictions
                    WHERE outcome = 'pending' AND check_after_date <= current_date
                    ORDER BY check_after_date ASC
                    LIMIT 20
                """).fetchall()

            if not rows:
                return json.dumps({"message": "没有待验证的预测", "checked": 0}, ensure_ascii=False)

            results = []
            due_count = 0
            for row in rows:
                due = row[12] is not None and row[12] <= today
                if due:
                    due_count += 1
                pred = {
                    "id": row[0],
                    "stock_code": row[1],
                    "stock_name": row[2],
                    "direction": row[3],
                    # 未知≠0：未设置目标/止损返回 null，不吐 0.0 冒充目标价
                    "target_price": row[4] if row[4] and row[4] > 0 else None,
                    "stop_loss": row[5] if row[5] and row[5] > 0 else None,
                    "timeframe_days": row[6],
                    "reasoning": row[7][:100] if row[7] else "",
                    "confidence": row[8],
                    "created_at": str(row[10]),
                    "check_after_date": str(row[12]) if row[12] else None,
                    "due": due,
                    "days_elapsed": (datetime.now() - row[10]).days,
                }
                results.append(pred)

            if stock_code:
                msg = (
                    f"找到 {due_count} 个已到期待验证预测，请用 verify_prediction 验证结果"
                    if due_count
                    else f"找到 {len(results)} 个未到期预测（含 check_after_date），到期前请勿提前验证"
                )
            else:
                msg = f"找到 {len(results)} 个待验证预测，请用 verify_prediction 验证结果"

            return json.dumps(
                {
                    "pending_predictions": results,
                    "total": len(results),
                    "due": due_count,
                    "message": msg,
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()

    def verify_prediction(
        self,
        prediction_id: str,
        actual_price: float = None,
        actual_return_pct: float = None,
        force: bool = False,
    ) -> str:
        """验证单个预测结果（一次性：pending 守卫，禁止重复改判与重复调权重）"""
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT direction, target_price, stop_loss, created_at, stock_code,
                       outcome, memory_ids_json, reasoning, check_after_date, confidence
                FROM predictions WHERE id = ?
            """,
                [prediction_id],
            ).fetchone()

            if not row:
                return json.dumps({"error": f"预测 {prediction_id} 不存在"}, ensure_ascii=False)

            (
                direction,
                target_price,
                stop_loss,
                created_at,
                stock_code,
                outcome,
                memory_ids_json,
                reasoning,
                check_after_date,
                confidence,
            ) = row

            # 一次性验证：已验证不得改判（否则准确率可被洗白、权重被重复计费）
            if outcome != "pending":
                return json.dumps(
                    {
                        "error": f"预测已验证过，结果不可覆盖（原结果: {outcome}）",
                        "outcome": outcome,
                    },
                    ensure_ascii=False,
                )

            # 未到期拒绝提前验证（当日波动会污染准确率）；显式 force 才放行
            today = datetime.now().date()
            if check_after_date is not None and check_after_date > today and not force:
                return json.dumps(
                    {
                        "error": f"预测未到期（check_after_date={check_after_date}），提前验证会污染准确率；确要验证请传 force=true",
                        "check_after_date": str(check_after_date),
                    },
                    ensure_ascii=False,
                )

            days_held = (datetime.now() - created_at).days

            # 实际价格：给了就必须是真价（0 元 = "未知"，经止损分支会伪造出 wrong）
            price = _clean_price(actual_price)
            if actual_price is not None and price is None:
                return json.dumps(
                    {"error": "actual_price 必须为正的真实成交价；未知价格请勿传 0，改传 actual_return_pct"},
                    ensure_ascii=False,
                )

            # 收益率清洗
            ret = None
            if actual_return_pct is not None:
                try:
                    ret = float(actual_return_pct)
                except (TypeError, ValueError):
                    return json.dumps({"error": f"actual_return_pct 必须为数字，收到 {actual_return_pct!r}"}, ensure_ascii=False)

            if price is None and ret is None:
                return json.dumps(
                    {"error": "缺少判定依据：请提供 actual_price（正数）或 actual_return_pct"},
                    ensure_ascii=False,
                )

            # entry_price 交叉核对：收益率与价格自相矛盾时拒绝入库
            entry_row = None
            try:
                entry_row = conn.execute("SELECT entry_price FROM predictions WHERE id = ?", [prediction_id]).fetchone()
            except Exception:
                entry_row = None
            entry_price = (entry_row[0] if entry_row else None) or None
            if entry_price and price is not None and ret is not None:
                implied = (price - entry_price) / entry_price * 100.0
                if abs(implied - ret) > 5.0:
                    return json.dumps(
                        {
                            "error": f"actual_price 与 actual_return_pct 矛盾：按 entry_price={entry_price} 反推收益 {implied:+.1f}%，与传入 {ret:+.1f}% 偏差过大"
                        },
                        ensure_ascii=False,
                    )

            has_target = target_price is not None and target_price > 0
            has_stop = stop_loss is not None and stop_loss > 0

            # 判定优先级：有目标/止损走价差判定（价格是第一事实）；否则走收益率带
            if has_target or has_stop:
                if price is None:
                    return json.dumps(
                        {"error": "该预测设置了目标价/止损价，请提供 actual_price 以便按价差判定"},
                        ensure_ascii=False,
                    )
                if direction == "bullish":
                    if has_target and price >= target_price:
                        outcome = "correct"
                    elif has_stop and price <= stop_loss:
                        outcome = "wrong"
                    elif has_target and not has_stop and price <= target_price * (1 - ADVERSE_BAND):
                        outcome = "wrong"  # 反向大幅偏离目标，证伪
                    else:
                        outcome = "partial"
                elif direction == "bearish":
                    if has_target and price <= target_price:
                        outcome = "correct"
                    elif has_stop and price >= stop_loss:
                        outcome = "wrong"
                    elif has_target and not has_stop and price >= target_price * (1 + ADVERSE_BAND):
                        outcome = "wrong"
                    else:
                        outcome = "partial"
                else:
                    outcome = "partial"
            else:
                if ret is None:
                    return json.dumps(
                        {
                            "error": "缺少 actual_return_pct 且预测未设置目标价/止损价，无法判定结果，请提供 actual_return_pct"
                        },
                        ensure_ascii=False,
                    )
                if direction == "bullish":
                    if ret > RETURN_BAND_PCT:
                        outcome = "correct"
                    elif ret < -RETURN_BAND_PCT:
                        outcome = "wrong"
                    else:
                        outcome = "partial"
                elif direction == "bearish":
                    if ret < -RETURN_BAND_PCT:
                        outcome = "correct"
                    elif ret > RETURN_BAND_PCT:
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
                WHERE id = ? AND outcome = 'pending'
            """,
                [price, ret, outcome, now, prediction_id],
            )

            # 更新关联记忆的重要性（只调一次；返回实际影响条数，不虚报）
            memory_ids = []
            try:
                memory_ids = json.loads(memory_ids_json) if memory_ids_json else []
                if not isinstance(memory_ids, list):
                    memory_ids = []
            except (json.JSONDecodeError, TypeError, ValueError):
                memory_ids = []

            memory_adjusted = 0
            if memory_ids and outcome in ("correct", "wrong"):
                adjustment = 0.1 if outcome == "correct" else -0.1
                for mid in memory_ids:
                    cur = conn.execute(
                        """
                        UPDATE long_term_memories
                        SET importance = LEAST(1.0, GREATEST(0.0, importance + ?)),
                            updated_at = ?
                        WHERE id = ?
                    """,
                        [adjustment, now, mid],
                    )
                    n = cur.fetchone()
                    memory_adjusted += n[0] if n else 0

            # 错误预测沉淀教训记忆（README 承诺的"从对错中学习"；learning_extracted 落真值）
            lesson_saved = False
            if outcome == "wrong":
                lesson_saved = self._save_lesson(conn, prediction_id, stock_code, direction, reasoning, days_held, price, ret)

            return json.dumps(
                {
                    "prediction_id": prediction_id,
                    "stock_code": stock_code,
                    "direction": direction,
                    "actual_price": price,
                    "actual_return_pct": ret,
                    "outcome": outcome,
                    "days_held": days_held,
                    "memory_adjusted": memory_adjusted,
                    "lesson_saved": lesson_saved,
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()

    def _save_lesson(self, conn, prediction_id, stock_code, direction, reasoning, days_held, price, ret) -> bool:
        """错误预测 → 教训记忆（learning 类型，半衰期 180 天）"""
        try:
            from deeppulse.agent.memory.manager import MemoryManager

            result_str = f"实际价格 {price}" if price is not None else ""
            if ret is not None:
                result_str += f"（收益 {ret:+.1f}%）"
            reason_str = (reasoning or "").strip()[:100]
            content = f"预测教训: {stock_code} {direction}方向预测持有{days_held}天验证为错误，{result_str}。"
            if reason_str:
                content += f"原理由: {reason_str}"
            mm = MemoryManager(db_path=self.db_path)
            mm.save_memory(
                content=content,
                memory_type="learning",
                keywords=str(stock_code or ""),
                tags="预测教训",
                importance=0.8,
                learned_what=f"{stock_code} {direction}方向判断被证伪",
                learned_why="预测结果与预期相反，说明当时的判断依据不足或忽略了反向信号",
                apply_when="再次分析同只股票或同类形态时，先回顾这次判断失效的原因",
            )
            conn.execute(
                "UPDATE predictions SET learning_extracted = TRUE WHERE id = ?", [prediction_id]
            )
            return True
        except Exception:
            return False

    def get_accuracy_stats(self) -> str:
        """预测准确率统计（双口径：全验证 vs 已决出胜负；expired 不入分母）"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT outcome, COUNT(*)
                FROM predictions
                WHERE outcome NOT IN ('pending', 'expired')
                GROUP BY outcome
            """).fetchall()

            stats = {row[0]: row[1] for row in rows}
            total = sum(stats.values())
            correct = stats.get("correct", 0)
            wrong = stats.get("wrong", 0)
            partial = stats.get("partial", 0)
            decided = correct + wrong

            # 按方向统计
            dir_rows = conn.execute("""
                SELECT direction, outcome, COUNT(*)
                FROM predictions
                WHERE outcome NOT IN ('pending', 'expired')
                GROUP BY direction, outcome
            """).fetchall()

            by_direction = {}
            for d, o, c in dir_rows:
                if d not in by_direction:
                    by_direction[d] = {}
                by_direction[d][o] = c

            # neutral 不进方向性准确率分母（中性判断对错无意义，别稀释统计）
            dir_total = sum(v for d, m in by_direction.items() if d != "neutral" for v in m.values())
            dir_correct = sum(m.get("correct", 0) for d, m in by_direction.items() if d != "neutral")

            expired_row = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE outcome = 'expired'"
            ).fetchone()

            return json.dumps(
                {
                    "total_verified": total,
                    "correct": correct,
                    "wrong": wrong,
                    "partial": partial,
                    "expired": expired_row[0] if expired_row else 0,
                    "accuracy": f"{correct / total * 100:.1f}%" if total > 0 else "N/A",
                    "accuracy_decided": f"{correct / decided * 100:.1f}%" if decided > 0 else "N/A",
                    "decided_total": decided,
                    "accuracy_directional": f"{dir_correct / dir_total * 100:.1f}%" if dir_total > 0 else "N/A",
                    "by_direction": by_direction,
                },
                ensure_ascii=False,
            )
        finally:
            conn.close()
