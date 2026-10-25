"""预测跟踪回归 — 方向白名单/未知≠0/一次性验证/到期守卫/教训沉淀/双口径准确率"""

import json
from datetime import datetime, timedelta

import pytest

from deeppulse.agent.memory import MemoryManager, PredictionTracker


def _pt(tmp_path) -> PredictionTracker:
    pt = PredictionTracker(db_path=tmp_path / "pred.duckdb")
    mm = MemoryManager(db_path=pt.db_path)
    mm.init_tables()
    return pt


def _due_pt(tmp_path) -> PredictionTracker:
    """造一个已到期的预测（直接把 check_after_date 拨到昨天）"""
    pt = _pt(tmp_path)
    return pt


def _make_due(pt, pred_id):
    conn = pt._conn()
    conn.execute(
        "UPDATE predictions SET check_after_date = ? WHERE id = ?",
        [(datetime.now() - timedelta(days=1)).date(), pred_id],
    )
    conn.close()


class TestSaveValidation:
    def test_direction_whitelist(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", direction="看涨"))
        assert "error" in r and "direction" in r["error"]
        r = json.loads(pt.save_prediction("600519", direction="Bullish"))
        assert r["status"] == "saved", "大小写归一后合法方向应放行"

    def test_timeframe_and_confidence_bounds(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", None, None, -10, "", 0.5))
        assert "error" in r and "timeframe_days" in r["error"]
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", None, None, 5, "", 99.0))
        assert "error" in r and "confidence" in r["error"]

    def test_unset_target_stop_stored_null_not_zero(self, tmp_path):
        """未知≠0：未设置目标/止损存 null，check 不得吐 0.0 冒充目标价"""
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", 0, 0, 5, "", 0.5))
        assert r["target_price"] is None and r["stop_loss"] is None
        check = json.loads(pt.check_predictions("600519"))
        pred = check["pending_predictions"][0]
        assert pred["target_price"] is None and pred["stop_loss"] is None

    def test_bullish_requires_target_above_stop(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", 100, 110, 5, "", 0.5))
        assert "error" in r


class TestCheckPredictions:
    def test_check_carries_due_flag_and_date(self, tmp_path):
        pt = _pt(tmp_path)
        pt.save_prediction("600519", "n", "direction", "bullish", None, None, 5, "", 0.5)
        check = json.loads(pt.check_predictions("600519"))
        pred = check["pending_predictions"][0]
        assert pred["check_after_date"] is not None
        assert pred["due"] is False, "未到期预测必须标记 due=false（不再诱导提前验证）"
        assert "未到期" in check["message"]

    def test_overdue_pending_is_listed_as_due(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", None, None, 5, "", 0.5))
        _make_due(pt, r["prediction_id"])
        check = json.loads(pt.check_predictions("600519"))
        assert check["pending_predictions"][0]["due"] is True

    def test_grace_expired_not_stuck_in_queue(self, tmp_path):
        """超宽限 pending 自动结转 expired，不再永远占着待验证队列"""
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", None, None, 5, "", 0.5))
        conn = pt._conn()
        conn.execute(
            "UPDATE predictions SET check_after_date = ? WHERE id = ?",
            [(datetime.now() - timedelta(days=90)).date(), r["prediction_id"]],
        )
        conn.close()
        check = json.loads(pt.check_predictions("600519"))
        assert check["checked"] == 0
        stats = json.loads(pt.get_accuracy_stats())
        assert stats["expired"] == 1
        assert stats["total_verified"] == 0, "expired 不得进准确率分母"


class TestVerify:
    def test_zero_price_rejected(self, tmp_path):
        """0 元价格 = '未知'，经止损分支会伪造出 wrong —— 未知≠0 铁律"""
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", 2100, 1900, 5, "", 0.5))
        _make_due(pt, r["prediction_id"])
        v = json.loads(pt.verify_prediction(r["prediction_id"], 0.0))
        assert "error" in v and "actual_price" in v["error"]

    def test_early_verify_requires_force(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", 2100, 1900, 5, "", 0.5))
        v = json.loads(pt.verify_prediction(r["prediction_id"], 2150.0))
        assert "error" in v and "未到期" in v["error"]
        v = json.loads(pt.verify_prediction(r["prediction_id"], 2150.0, force=True))
        assert v["outcome"] == "correct"

    def test_reverify_rejected_outcome_immutable(self, tmp_path):
        """一次性验证：correct 不得被后一次 verify 改判成 wrong、权重不得重复计费"""
        pt = _pt(tmp_path)
        mm = MemoryManager(db_path=pt.db_path)
        mem = json.loads(mm.save_memory("关联依据", "insight", "依据", "", 0.5))
        r = json.loads(
            pt.save_prediction("600519", "n", "direction", "bullish", 2100, 1900, 5, "", 0.5, [mem["memory_id"]])
        )
        pid = r["prediction_id"]
        _make_due(pt, pid)
        v1 = json.loads(pt.verify_prediction(pid, 2150.0, 7.5, force=True))
        assert v1["outcome"] == "correct" and v1["memory_adjusted"] == 1
        v2 = json.loads(pt.verify_prediction(pid, 100.0, -50.0))
        assert "error" in v2 and v2.get("outcome") == "correct", "已验证结果不可覆盖"
        lst = json.loads(mm.list_memories())
        assert lst["results"][0]["importance"] == pytest.approx(0.6), "权重被二次调整"

    def test_target_only_adverse_move_falsified(self, tmp_path):
        """仅目标价无止损：反向大幅偏离必须可证伪（否则暴跌也永远 partial）"""
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", 100, None, 5, "", 0.5))
        _make_due(pt, r["prediction_id"])
        v = json.loads(pt.verify_prediction(r["prediction_id"], 1.0))
        assert v["outcome"] == "wrong"

    def test_price_path_wins_over_return_pct(self, tmp_path):
        """设了目标/止损时价差判定优先（收益率不得绕过已触及的目标价）"""
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "bullish", 20, None, 5, "", 0.5))
        _make_due(pt, r["prediction_id"])
        v = json.loads(pt.verify_prediction(r["prediction_id"], 21.0, 2.5))
        assert v["outcome"] == "correct", "价格已达目标价，收益率 +2.5% 不得判 partial"

    def test_return_band_and_memory_adjusted_honesty(self, tmp_path):
        pt = _pt(tmp_path)
        mm = MemoryManager(db_path=pt.db_path)
        mem = json.loads(mm.save_memory("另一依据", "insight", "依据2", "", 0.5))
        r = json.loads(
            pt.save_prediction("000001", "n", "direction", "bullish", None, None, 5, "", 0.5, [mem["memory_id"]])
        )
        _make_due(pt, r["prediction_id"])
        v = json.loads(pt.verify_prediction(r["prediction_id"], None, 1.0))
        assert v["outcome"] == "partial"
        assert v["memory_adjusted"] == 0, "partial 不得虚报 memory_adjusted"

    def test_wrong_saves_lesson_and_marks_learning_extracted(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("000001", "n", "direction", "bullish", None, None, 5, "看好放量突破", 0.5))
        _make_due(pt, r["prediction_id"])
        v = json.loads(pt.verify_prediction(r["prediction_id"], None, -20.0))
        assert v["outcome"] == "wrong"
        assert v["lesson_saved"] is True
        conn = pt._conn()
        flag = conn.execute(
            "SELECT learning_extracted FROM predictions WHERE id = ?", [r["prediction_id"]]
        ).fetchone()[0]
        lessons = conn.execute(
            "SELECT COUNT(*) FROM long_term_memories WHERE memory_type = 'learning' AND is_archived = FALSE"
        ).fetchone()[0]
        conn.close()
        assert flag is True, "learning_extracted 死列必须落真值"
        assert lessons == 1, "错误预测必须沉淀教训记忆（README 承诺）"

    def test_entry_price_cross_check(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(
            pt.save_prediction("600519", "n", "direction", "bullish", None, None, 5, "", 0.5, None, 100.0)
        )
        _make_due(pt, r["prediction_id"])
        v = json.loads(pt.verify_prediction(r["prediction_id"], 50.0, 5.0))
        assert "error" in v and "矛盾" in v["error"], "价格与收益率自相矛盾必须拒绝入库"

    def test_neutral_and_missing_evidence(self, tmp_path):
        pt = _pt(tmp_path)
        r = json.loads(pt.save_prediction("600519", "n", "direction", "neutral", None, None, 5, "", 0.5))
        _make_due(pt, r["prediction_id"])
        v = json.loads(pt.verify_prediction(r["prediction_id"]))
        assert "error" in v, "无目标/止损且无收益率时不得伪造判定"
        v = json.loads(pt.verify_prediction(r["prediction_id"], None, 5.0, force=True))
        assert v["outcome"] == "partial"


class TestAccuracyStats:
    def test_dual_accuracy_and_neutral_exclusion(self, tmp_path):
        pt = _pt(tmp_path)
        for direction, ret in (("bullish", 10.0), ("bullish", -10.0), ("bullish", 1.0), ("neutral", 10.0)):
            r = json.loads(pt.save_prediction("600519", "n", "direction", direction, None, None, 5, "", 0.5))
            _make_due(pt, r["prediction_id"])
            pt.verify_prediction(r["prediction_id"], None, ret)
        stats = json.loads(pt.get_accuracy_stats())
        assert stats["total_verified"] == 4
        assert stats["correct"] == 1 and stats["wrong"] == 1 and stats["partial"] == 2
        assert stats["decided_total"] == 2
        assert stats["accuracy_decided"] == "50.0%"
        assert stats["accuracy_directional"] != "N/A", "neutral 不进方向性分母，但方向性口径必须给出"
