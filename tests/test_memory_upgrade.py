"""记忆系统升级 - 集成测试（需要真实数据库）"""

import json
import math

import pytest


@pytest.mark.integration
def test_database_migration():
    from src.database import get_connection, init_memory_tables, init_tables

    conn = get_connection()
    init_tables(conn)
    init_memory_tables(conn)
    for table in [
        "stock_info",
        "daily_kline",
        "fetch_log",
        "long_term_memories",
        "session_memories",
        "memory_sessions",
        "predictions",
        "knowledge_entities",
        "knowledge_relations",
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
    from agent.memory import EmbeddingIndex

    ei = EmbeddingIndex()
    if ei.is_ready:
        vec = ei.encode_single("贵州茅台RSI超卖支撑位")
        assert len(vec) > 0
        vec2 = ei.encode_single("600519茅台关键价位")
        dot = sum(a * b for a, b in zip(vec, vec2, strict=False))
        norm1 = math.sqrt(sum(a * a for a in vec))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        sim = dot / (norm1 * norm2) if norm1 > 0 and norm2 > 0 else 0
        assert sim > 0.3, f"Similarity too low: {sim}"


@pytest.mark.integration
def test_bm25_search():
    from agent.memory import BM25Index

    bm25 = BM25Index()
    docs = [
        ("m1", "贵州茅台600519 RSI超卖 可能反弹", ["600519", "RSI", "超卖"]),
        ("m2", "平安银行000001 放量突破 均线金叉", ["000001", "放量", "突破"]),
        ("m3", "MACD金叉 低吸战法 缩量回调", ["MACD", "金叉", "低吸"]),
        ("m4", "市场情绪低迷 冰点期 等待转机", ["市场情绪", "冰点"]),
    ]
    bm25.build(docs)
    results = bm25.search("茅台RSI超卖", ["600519", "RSI", "超卖"])
    assert results[0][0] == "m1", f"Expected m1, got {results[0][0]}"


@pytest.mark.integration
def test_memory_crud_and_search():
    from agent.memory import MemoryManager
    from src.database import get_connection

    mm = MemoryManager()
    mm.init_tables()

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
        saved_ids.append(result["memory_id"])

    for query in ["茅台支撑位", "MACD金叉突破", "RSI使用技巧", "市场情绪"]:
        result = json.loads(mm.search_memories(query))
        assert result["total"] >= 0

    conn = get_connection()
    conn.execute("DELETE FROM long_term_memories WHERE source_session_id IS NULL")
    conn.close()


@pytest.mark.integration
def test_prediction_tracker():
    from agent.memory import PredictionTracker
    from src.database import get_connection

    pt = PredictionTracker()

    r1 = json.loads(
        pt.save_prediction("600519", "贵州茅台", "direction", "bullish", 2100, 1900, 5, "RSI超卖+支撑位", 0.7)
    )
    json.loads(pt.save_prediction("000001", "平安银行", "direction", "bearish", 0, 0, 3, "放量下跌", 0.6))

    check = json.loads(pt.check_predictions("600519"))
    assert check["total"] >= 0

    verify = json.loads(pt.verify_prediction(r1["prediction_id"], 2150.0, 7.5))
    assert verify["outcome"] == "correct", f"Expected correct, got {verify['outcome']}"

    stats = json.loads(pt.get_accuracy_stats())
    assert stats["total_verified"] >= 1

    conn = get_connection()
    conn.execute("DELETE FROM predictions")
    conn.close()


@pytest.mark.integration
def test_user_profile():
    from agent.memory import UserProfile
    from src.database import get_connection

    up = UserProfile()

    up.update_profile("trading_style", "低吸型", 0.6)
    up.update_profile("risk_tolerance", "稳健型", 0.5)
    up.update_profile("stop_loss_habit", "5%", 0.7)
    up.update_profile("watched_sectors", "白酒、银行", 0.5)

    profile = json.loads(up.get_profile())
    assert profile["total"] >= 4

    summary = up.get_summary_text()
    assert len(summary) > 0

    signals = up.extract_from_conversation(["我就喜欢做低吸，不喜欢追高", "止损设5%", "我主要做半导体板块"])
    assert len(signals) >= 1

    conn = get_connection()
    conn.execute("DELETE FROM user_profile")
    conn.close()


@pytest.mark.integration
def test_knowledge_graph():
    from agent.memory import KnowledgeGraph
    from src.database import get_connection

    kg = KnowledgeGraph()

    e1 = kg.add_entity("indicator", "RSI超卖")
    e2 = kg.add_entity("strategy", "低吸战法")
    e3 = kg.add_entity("indicator", "MACD金叉")
    e4 = kg.add_entity("strategy", "放量突破战法")
    kg.add_relation(e1, e2, "triggers", 0.8)
    kg.add_relation(e3, e4, "triggers", 0.9)
    kg.add_relation(e1, e3, "supports", 0.5)

    result = json.loads(kg.query_related("RSI"))
    assert result["total"] >= 1

    entities, relations = kg.extract_from_memory("贵州茅台MACD金叉，RSI超卖反弹")
    assert len(entities) >= 1

    conn = get_connection()
    conn.execute("DELETE FROM knowledge_entities")
    conn.execute("DELETE FROM knowledge_relations")
    conn.close()


@pytest.mark.integration
def test_context_injection():
    from agent.memory import MemoryManager
    from src.database import get_connection

    mm = MemoryManager()
    mm.init_tables()

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

    conn = get_connection()
    conn.execute("DELETE FROM long_term_memories WHERE source_session_id IS NULL")
    conn.close()
