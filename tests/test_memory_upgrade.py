"""记忆系统升级测试 —— 全部走 tmp 隔离库，绝不触碰真实 db/astock.duckdb

历史事故备忘：本文件曾用 MemoryManager() 无参构造直连真实库并在收尾
DELETE 真实记忆/预测/画像（工具写入的 source_session_id IS NULL 记忆 100% 中招）。
现全部改为 tmp_path 隔离库；pyproject addopts 亦默认排除 integration 标记用例。
仅 test_database_migration / test_embedding_index 真正需要真实库/模型，保留 integration 标记。
"""

import json
import math

import pytest

from deeppulse.agent.memory import MemoryManager, PredictionTracker, UserProfile
from deeppulse.src.database import get_connection, init_memory_tables


def _mem_db(tmp_path) -> MemoryManager:
    """隔离库上的 MemoryManager（含建表）"""
    mm = MemoryManager(db_path=tmp_path / "mem.duckdb")
    mm.init_tables()
    return mm


@pytest.mark.integration
def test_database_migration():
    from deeppulse.src.database import init_tables

    conn = get_connection()
    init_tables(conn)
    init_memory_tables(conn)
    for table in [
        "stock_info",
        "daily_kline",
        "fetch_log",
        "long_term_memories",
        "memory_sessions",
        "predictions",
        "user_profile",
    ]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count >= 0
    result = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'long_term_memories' AND column_name = 'embedding'"
    ).fetchone()
    assert result is not None, "embedding column missing"
    conn.close()


@pytest.mark.integration
def test_embedding_index():
    from deeppulse.agent.memory import EmbeddingIndex

    ei = EmbeddingIndex()
    if not ei.is_ready:
        pytest.skip("embedding 未配置（provider=none 或模型不可用）")
    vec = ei.encode_single("贵州茅台RSI超卖支撑位")
    assert len(vec) > 0
    vec2 = ei.encode_single("600519茅台关键价位")
    dot = sum(a * b for a, b in zip(vec, vec2, strict=True))
    norm1 = math.sqrt(sum(a * a for a in vec))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    sim = dot / (norm1 * norm2) if norm1 > 0 and norm2 > 0 else 0
    assert sim > 0.3, f"Similarity too low: {sim}"


def test_bm25_search():
    from deeppulse.agent.memory import BM25Index

    bm25 = BM25Index()
    docs = [
        ("m1", "贵州茅台600519 RSI超卖 可能反弹", ["600519", "RSI", "超卖"]),
        ("m2", "平安银行000001 放量突破 均线金叉", ["000001", "放量", "突破"]),
        ("m3", "MACD金叉 低吸战法 缩量回调", ["MACD", "金叉", "低吸"]),
        ("m4", "市场情绪低迷 冰点期 等待转机", ["市场情绪", "冰点"]),
    ]
    bm25.build(docs)
    results = bm25.search("茅台RSI超卖", ["600519", "RSI", "超卖"])
    assert results and results[0][0] == "m1", f"Expected m1, got {results[:3]}"


def test_memory_crud_and_search(tmp_path):
    mm = _mem_db(tmp_path)

    memories_data = [
        (
            "贵州茅台600519在2000元有强支撑，RSI接近超卖区，可以低吸",
            "insight",
            "600519,贵州茅台,RSI,支撑位",
            "技术分析,白酒",
            0.7,
        ),
        (
            "平安银行000001放量突破MA20，MACD金叉，短线看涨",
            "insight",
            "000001,平安银行,MACD,突破",
            "技术分析,银行",
            0.6,
        ),
        ("用户偏好低吸操作，不喜欢追高", "preference", "低吸,交易风格", "用户偏好", 0.8),
        ("RSI超卖时要结合成交量看，不能单独使用", "learning", "RSI,成交量", "用户教学", 0.8),
        ("市场情绪处于冰点期，应等待转机再入场", "fact", "市场情绪,冰点", "市场分析", 0.5),
    ]
    saved_ids = []
    for content, mtype, kw, tags, imp in memories_data:
        result = json.loads(mm.save_memory(content, mtype, kw, tags, imp))
        assert result["status"] == "saved"
        saved_ids.append(result["memory_id"])

    # 检索必须真的命中，不允许 total>=0 恒真断言
    result = json.loads(mm.search_memories("茅台支撑位"))
    assert result["total"] >= 1
    assert any("600519" in m["content"] for m in result["results"])

    result = json.loads(mm.search_memories("MACD金叉突破"))
    assert result["total"] >= 1
    assert any("000001" in m["content"] for m in result["results"])

    # 按类型过滤不串型
    result = json.loads(mm.search_memories("RSI", memory_type="learning"))
    assert result["total"] >= 1
    assert all(m["memory_type"] == "learning" for m in result["results"])

    # 命中即更新访问计数
    result = json.loads(mm.search_memories("低吸"))
    hit = next((m for m in result["results"] if "低吸操作" in m["content"]), None)
    assert hit is not None
    assert hit["access_count"] >= 1


def test_prediction_tracker(tmp_path):
    pt = PredictionTracker(db_path=tmp_path / "mem.duckdb")
    conn = get_connection(pt.db_path)
    init_memory_tables(conn)
    conn.close()

    r1 = json.loads(
        pt.save_prediction("600519", "贵州茅台", "direction", "bullish", 2100, 1900, 5, "RSI超卖+支撑位", 0.7)
    )
    json.loads(pt.save_prediction("000001", "平安银行", "direction", "bearish", 0, 0, 3, "放量下跌", 0.6))

    check = json.loads(pt.check_predictions("600519"))
    assert check["total"] == 1
    assert check["pending_predictions"][0]["stock_code"] == "600519"

    # 未到期默认拒绝提前验证（防当日波动污染准确率），force 显式放行
    early = json.loads(pt.verify_prediction(r1["prediction_id"], 2150.0, 7.5))
    assert "error" in early and "未到期" in early["error"]
    verify = json.loads(pt.verify_prediction(r1["prediction_id"], 2150.0, 7.5, force=True))
    assert verify["outcome"] == "correct", f"Expected correct, got {verify['outcome']}"

    stats = json.loads(pt.get_accuracy_stats())
    assert stats["total_verified"] == 1
    assert stats["correct"] == 1


def test_user_profile(tmp_path):
    up = UserProfile(db_path=tmp_path / "mem.duckdb")
    conn = get_connection(up.db_path)
    init_memory_tables(conn)
    conn.close()

    up.update_profile("trading_style", "低吸型", 0.6)
    up.update_profile("risk_tolerance", "稳健型", 0.5)
    up.update_profile("stop_loss_habit", "5%", 0.7)
    up.update_profile("watched_sectors", "白酒、银行", 0.5)

    profile = json.loads(up.get_profile())
    assert profile["total"] == 4
    assert profile["profile"]["trading_style"]["value"] == "低吸型"

    summary = up.get_summary_text()
    assert "交易风格" in summary
    assert "低吸型" in summary

    signals = up.extract_from_conversation(["我就喜欢做低吸，不喜欢追高", "止损设5%", "我主要做半导体板块"])
    assert any(s["key"] == "trading_style" and s["value"] == "低吸型" for s in signals)
    assert any(s["key"] == "stop_loss_habit" for s in signals)


def test_context_injection(tmp_path):
    mm = _mem_db(tmp_path)

    mm.save_memory("茅台RSI超卖", "insight", "600519,RSI", "test", 0.7)
    mm.save_memory(
        "用户偏好低吸",
        "learning",
        "低吸",
        "test",
        0.8,
        learned_what="低吸为主",
        learned_why="风险低",
        apply_when="RSI超卖时",
    )

    ctx = mm.get_context_block("茅台短线分析")
    assert len(ctx) > 0, "Context block is empty"
    assert "相关记忆" in ctx or "用户教学" in ctx
    assert "低吸为主" in ctx
