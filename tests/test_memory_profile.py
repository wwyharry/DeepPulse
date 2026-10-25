"""用户画像回归 — 置信度夹取/冲突保高置信/否定窗口/时序覆盖/多值合并/词表对齐"""

import json

import pytest

from deeppulse.agent.memory import UserProfile


def _up(tmp_path) -> UserProfile:
    up = UserProfile(db_path=tmp_path / "profile.duckdb")
    from deeppulse.src.database import get_connection, init_memory_tables

    conn = get_connection(up.db_path)
    init_memory_tables(conn)
    conn.close()
    return up


class TestUpdateProfile:
    def test_confidence_clamped_with_flag(self, tmp_path):
        up = _up(tmp_path)
        r = json.loads(up.update_profile("position_sizing", "半仓滚动", 100.0))
        assert r["confidence"] == pytest.approx(1.0)
        assert r.get("confidence_clamped") is True, "越界置信度必须夹取且回告"
        r = json.loads(up.update_profile("profit_target", "10%", -5.0))
        assert r["confidence"] == pytest.approx(0.0)

    def test_low_confidence_conflict_cannot_flip(self, tmp_path):
        """低置信新值不得推翻高置信旧共识（一次口误不能翻转已确立画像）"""
        up = _up(tmp_path)
        up.update_profile("risk_tolerance", "激进型", 0.95)
        r = json.loads(up.update_profile("risk_tolerance", "保守型", 0.1))
        assert r["value"] == "激进型", f"0.1 置信度口误不应覆盖 0.95 共识: {r}"
        assert r["confidence"] < 0.95, "冲突证据应降低整体置信度"

    def test_higher_confidence_conflict_switches(self, tmp_path):
        up = _up(tmp_path)
        up.update_profile("trading_style", "低吸型", 0.3)
        r = json.loads(up.update_profile("trading_style", "打板型", 0.9))
        assert r["value"] == "打板型", "更强的新证据可以换值"

    def test_multi_value_keys_merge_not_overwrite(self, tmp_path):
        up = _up(tmp_path)
        up.update_profile("preferred_indicators", "MACD", 0.4)
        r = json.loads(up.update_profile("preferred_indicators", "KDJ", 0.4))
        assert "MACD" in r["value"] and "KDJ" in r["value"], f"多值键被整值覆盖: {r['value']}"

    def test_unknown_key_and_empty_value_rejected(self, tmp_path):
        up = _up(tmp_path)
        assert "error" in json.loads(up.update_profile("favorite_color", "红", 0.5))
        assert "error" in json.loads(up.update_profile("trading_style", "", 0.5))

    def test_same_value_boosts_confidence_capped_at_one(self, tmp_path):
        up = _up(tmp_path)
        up.update_profile("trading_style", "低吸型", 0.97)
        r = json.loads(up.update_profile("trading_style", "低吸型", 0.97))
        assert r["confidence"] == pytest.approx(1.0), "同值加成不得超 1.0"
        r2 = json.loads(up.update_profile("trading_style", "低吸型", 0.97))
        assert r2["confidence"] <= 1.0

    def test_source_memory_ids_merge_tolerates_bad_json(self, tmp_path):
        up = _up(tmp_path)
        up.update_profile("trading_style", "低吸型", 0.5, source_memory_ids=["m1"])
        conn = up._conn()
        conn.execute("UPDATE user_profile SET source_memory_ids = '{oops' WHERE key = 'trading_style'")
        conn.close()
        r = json.loads(up.update_profile("trading_style", "低吸型", 0.5, source_memory_ids=["m2"]))
        assert r["status"] == "updated", "坏 JSON 预置行不得炸更新"


class TestExtract:
    def test_negation_blocks_signal(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["我不喜欢打板，也不追高"])
        assert not any(s["key"] == "trading_style" for s in signals), f"否定句误判: {signals}"

    def test_negation_does_not_cross_punctuation(self):
        """「别打板，我做低吸」——否定只作用于打板，低吸照常成立"""
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["别打板，我做低吸"])
        styles = [s for s in signals if s["key"] == "trading_style"]
        assert styles and styles[0]["value"] == "低吸型", signals

    def test_recent_statement_wins(self):
        """「以前低吸，现在改打板」→ 打板型（历史顺序偏置回归）"""
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["我以前做低吸，现在我改打板了"])
        styles = [s for s in signals if s["key"] == "trading_style"]
        assert styles and styles[0]["value"] == "打板型", signals

    def test_negation_on_risk(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["我不能满仓，别推荐激进操作"])
        assert not any(s["key"] == "risk_tolerance" for s in signals), signals

    def test_unknown_negation_target_positive(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["止损设5%"])
        assert any(s["key"] == "stop_loss_habit" for s in signals)


class TestSummaryText:
    def test_low_confidence_filtered_and_budget_respected(self, tmp_path):
        up = _up(tmp_path)
        up.update_profile("trading_style", "低吸型", 0.2)  # < 0.3 不进摘要
        up.update_profile("risk_tolerance", "保守型", 0.9)
        summary = up.get_summary_text()
        assert "风险偏好" in summary
        assert "交易风格" not in summary, "低置信条目不得注入"
        short = up.get_summary_text(max_chars=5)
        assert len(short) <= 5


class TestVocabAlignment:
    def test_conservative_value_hits_reasoning_matcher(self):
        """提取层输出「保守型」必须命中动态提示的稳健分支（两处词表漂移回归）"""
        from deeppulse.agent.reasoning import build_dynamic_system_prompt

        prompt = build_dynamic_system_prompt(
            "BASE", market_state=None, user_profile={"risk_tolerance": {"value": "保守型", "confidence": 0.9}}
        )
        assert "操作建议偏向稳健" in prompt, "保守型用户拿不到稳健适配"

    def test_profile_section_carries_uncertainty_caveat(self):
        from deeppulse.agent.reasoning import build_dynamic_system_prompt

        prompt = build_dynamic_system_prompt(
            "BASE", market_state=None, user_profile={"trading_style": {"value": "低吸型", "confidence": 0.9}}
        )
        assert "可能有误" in prompt, "画像注入须带推测口径"
