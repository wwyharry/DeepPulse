"""memory_tools 13 工具包装层测试 — schema/入参校验/信封/会话关联/锁兜底

此前 13 个工具零直接测试（只测了分发表接线）。这里逐工具钉最小契约。
"""

import inspect
import json

import pytest

from deeppulse.agent.memory import MemoryManager
from deeppulse.agent.tools import memory_tools


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """tmp 隔离库的 MemoryManager，注入 get_memory()（绝不触碰真实库）"""
    from deeppulse.agent.tools import _shared

    _shared.set_current_session(None)  # 清跨模块泄漏的会话绑定
    mm = MemoryManager(db_path=tmp_path / "tools.duckdb")
    mm.init_tables()
    monkeypatch.setattr(memory_tools, "get_memory", lambda: mm)
    yield mm
    _shared.set_current_session(None)


class TestSaveMemoryTool:
    def test_save_and_dedup(self, mem):
        r = json.loads(memory_tools.save_memory("工具保存的结论", "insight", "工具", "测试", 0.6))
        assert r["status"] == "saved" and r["memory_type"] == "insight"
        r2 = json.loads(memory_tools.save_memory("工具保存的结论", "insight", "工具", "测试", 0.6))
        assert r2["status"] == "existing"

    def test_learning_default_importance(self, mem):
        """不传 importance 时 learning 默认 0.7（旧魔法哨兵'显式0.5被改0.7'已死）"""
        r = json.loads(memory_tools.save_memory("教学内容", "learning"))
        lst = json.loads(memory_tools.list_memories())
        assert lst["results"][0]["importance"] == pytest.approx(0.7)
        r = json.loads(memory_tools.save_memory("显式0.5的教学", "learning", importance=0.5))
        lst = json.loads(memory_tools.list_memories(memory_type="learning"))
        imp = [m["importance"] for m in lst["results"] if m["id"] == r["memory_id"]][0]
        assert imp == pytest.approx(0.5), "显式 0.5 不得被静默抬到 0.7"

    def test_validation_errors(self, mem):
        assert "error" in json.loads(memory_tools.save_memory(""))
        assert "error" in json.loads(memory_tools.save_memory("x" * 3000))
        assert "error" in json.loads(memory_tools.save_memory("内容", "mystery"))
        assert "error" in json.loads(memory_tools.save_memory("内容", importance=99.5))


class TestSearchListTool:
    def test_search_and_bounds(self, mem):
        memory_tools.save_memory("低吸策略偏好", "preference", "低吸", "", 0.6)
        r = json.loads(memory_tools.search_memory("低吸"))
        assert r["total"] >= 1
        assert "error" in json.loads(memory_tools.search_memory(""))
        with pytest.raises(ValueError):
            memory_tools.search_memory("x", top_k=-1)

    def test_list_bounds_and_type(self, mem):
        assert "error" in json.loads(memory_tools.list_memories(memory_type="nope"))
        r = json.loads(memory_tools.list_memories(limit=1))
        assert "returned" in r and "total" in r


class TestUpdateDeleteTool:
    def test_update_noop_and_delete_honesty(self, mem):
        saved = json.loads(memory_tools.save_memory("待改内容", "insight", "旧词", "旧标签", 0.5))
        r = json.loads(memory_tools.update_memory(saved["memory_id"]))
        assert r["status"] == "noop"
        r = json.loads(memory_tools.update_memory(saved["memory_id"], tags=""))
        assert r["status"] == "updated"
        r = json.loads(memory_tools.delete_memory(saved["memory_id"]))
        assert r["status"] == "archived"
        r = json.loads(memory_tools.delete_memory(saved["memory_id"]))
        assert "error" in r, "不存在的 id 不得谎报成功"

    def test_update_importance_bounds(self, mem):
        saved = json.loads(memory_tools.save_memory("重要度边界", "insight", "边界", "", 0.5))
        assert "error" in json.loads(memory_tools.update_memory(saved["memory_id"], importance=2.0))


class TestSessionContextTool:
    def test_no_leading_separator(self, mem):
        """仅 topic/notes 时不得拼出以 ' | ' 开头的脏内容"""
        r = json.loads(memory_tools.save_session_context("", "复盘", "临时"))
        assert r["status"] == "saved"
        lst = json.loads(memory_tools.list_memories(memory_type="context"))
        assert not lst["results"][0]["content"].startswith(" | "), lst["results"][0]["content"]
        assert "主题: 复盘" in lst["results"][0]["content"]

    def test_empty_is_error(self, mem):
        assert "error" in json.loads(memory_tools.save_session_context("", "", ""))


class TestPredictionTools:
    def test_save_links_current_session_memories(self, mem):
        """关联记忆只取当前会话新增，不得摸跨会话的共享累积列表"""
        mem.start_session("s1")
        memory_tools.save_memory("会话一依据", "insight", "依据", "", 0.5)
        mem.start_session("s2")
        memory_tools.save_memory("会话二依据", "insight", "依据", "", 0.5)
        r = json.loads(memory_tools.save_prediction("600519", "bullish"))
        conn = mem._conn()
        row = conn.execute("SELECT memory_ids_json FROM predictions WHERE id = ?", [r["prediction_id"]]).fetchone()
        conn.close()
        linked = json.loads(row[0])
        lst = json.loads(memory_tools.list_memories())
        by_content = {m["content"]: m["id"] for m in lst["results"]}
        assert linked == [by_content["会话二依据"]], f"跨会话污染: {linked}"

    def test_explicit_memory_ids_win(self, mem):
        r = json.loads(memory_tools.save_prediction("600519", "bullish", memory_ids="m1, m2"))
        conn = mem._conn()
        row = conn.execute("SELECT memory_ids_json FROM predictions WHERE id = ?", [r["prediction_id"]]).fetchone()
        conn.close()
        assert json.loads(row[0]) == ["m1", "m2"]

    def test_verify_optional_price_and_rules_disclosed(self, mem):
        r = json.loads(memory_tools.save_prediction("600519", "bullish", timeframe_days=1))
        v = json.loads(memory_tools.verify_prediction(r["prediction_id"], actual_return_pct=8.0, force=True))
        assert v["outcome"] == "correct"
        # 工具描述必须披露判定规则（±3% 带）
        desc = next(t for t in memory_tools.TOOL_DEFINITIONS if t["function"]["name"] == "verify_prediction")
        assert "3%" in desc["function"]["description"]

    def test_check_and_stats(self, mem):
        memory_tools.save_prediction("600519", "bullish")
        check = json.loads(memory_tools.check_predictions("600519"))
        assert check["total"] == 1
        stats = json.loads(memory_tools.prediction_stats())
        assert "accuracy_decided" in stats


class TestProfileTools:
    def test_update_profile_validation(self, mem):
        assert "error" in json.loads(memory_tools.update_user_profile("favorite_color", "红"))
        assert "error" in json.loads(memory_tools.update_user_profile("trading_style", "低吸型", confidence=3.0))
        r = json.loads(memory_tools.update_user_profile("trading_style", "低吸型", 0.6))
        assert r["status"] == "updated"
        g = json.loads(memory_tools.get_user_profile())
        assert g["profile"]["trading_style"]["value"] == "低吸型"


class TestRecordLearningTool:
    def test_learning_envelope_and_fields(self, mem):
        r = json.loads(
            memory_tools.record_learning("低吸优于追高", "回撤小", "震荡市", "user_correction", "RSI", "600519", 0.8)
        )
        assert r["status"] == "saved" and r["memory_type"] == "learning"
        hit = json.loads(memory_tools.search_memory("低吸", memory_type="learning"))
        m = hit["results"][0]
        assert m["learned_what"] == "低吸优于追高"
        assert m["learned_why"] == "回撤小" and m["apply_when"] == "震荡市"

    def test_validation(self, mem):
        assert "error" in json.loads(memory_tools.record_learning("", "y", "z"))
        assert "error" in json.loads(memory_tools.record_learning("x", "y", "z", importance=9))


class TestSchemaConsistency:
    def test_definitions_match_signatures(self):
        """TOOL_DEFINITIONS 参数名/必填与函数签名一致（防 schema 漂移）"""
        for tool in memory_tools.TOOL_DEFINITIONS:
            fn = tool["function"]
            func = memory_tools.TOOL_DISPATCH[fn["name"]]
            sig_params = [
                p.name
                for p in inspect.signature(func).parameters.values()
                if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            ]
            props = set(fn["parameters"].get("properties", {}))
            required = set(fn["parameters"].get("required", []))
            assert props == set(sig_params), f"{fn['name']} schema 参数与签名不符: {props} vs {sig_params}"
            assert required <= props, f"{fn['name']} required 含未声明参数"


class TestSessionBinding:
    def test_contextvar_session_id_lands_on_memory(self, mem):
        """工具保存走 ContextVar 会话绑定：多 Web 会话并发不串号（不靠共享实例内存态）"""
        from deeppulse.agent.tools import _shared

        _shared.set_current_session("web-a")
        memory_tools.save_memory("会话A的结论", "insight", "会话A", "", 0.5)
        _shared.set_current_session("web-b")
        memory_tools.save_memory("会话B的结论", "insight", "会话B", "", 0.5)
        _shared.set_current_session(None)
        assert mem.get_session_memory_ids("web-a") != mem.get_session_memory_ids("web-b")
        conn = mem._conn()
        rows = conn.execute(
            "SELECT content, source_session_id FROM long_term_memories WHERE is_archived = FALSE"
        ).fetchall()
        conn.close()
        by_content = dict(rows)
        assert by_content["会话A的结论"] == "web-a"
        assert by_content["会话B的结论"] == "web-b"

    def test_save_prediction_links_bound_session(self, mem):
        """save_prediction 关联记忆取 ContextVar 绑定会话，而非共享累积列表"""
        from deeppulse.agent.tools import _shared

        _shared.set_current_session("web-c")
        memory_tools.save_memory("会话C依据", "insight", "会话C", "", 0.5)
        _shared.set_current_session("web-d")
        memory_tools.save_memory("会话D依据", "insight", "会话D", "", 0.5)
        r = json.loads(memory_tools.save_prediction("600519", "bullish"))
        conn = mem._conn()
        row = conn.execute(
            "SELECT memory_ids_json FROM predictions WHERE id = ?", [r["prediction_id"]]
        ).fetchone()
        conn.close()
        linked = json.loads(row[0])
        lst = json.loads(memory_tools.list_memories())
        by_content = {m["content"]: m["id"] for m in lst["results"]}
        assert linked == [by_content["会话D依据"]], f"会话串号: {linked}"
        _shared.set_current_session(None)


class TestDbGuard:
    def test_lock_error_mapped(self, mem, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("IOException: lock is held by another process")

        monkeypatch.setattr(mem, "list_memories", boom)
        r = json.loads(memory_tools.list_memories())
        assert "error" in r and "数据库繁忙" in r["error"]
