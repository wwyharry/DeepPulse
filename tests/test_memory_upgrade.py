"""记忆系统升级 - 完整测试"""
import json
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_database_migration():
    print('\n[1/8] 数据库迁移测试...')
    from src.database import get_connection, init_tables, init_memory_tables
    conn = get_connection()
    init_tables(conn)
    init_memory_tables(conn)
    for table in ['stock_info', 'daily_kline', 'fetch_log', 'long_term_memories',
                  'session_memories', 'memory_sessions',
                  'predictions', 'knowledge_entities', 'knowledge_relations', 'user_profile']:
        count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        print(f'  {table}: OK ({count} rows)')
    result = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'long_term_memories' AND column_name = 'embedding'"
    ).fetchone()
    print(f'  embedding column: {"OK" if result else "MISSING"}')
    conn.close()
    print('  PASS')


def test_embedding_index():
    print('\n[2/8] Embedding 索引测试...')
    from agent.memory import EmbeddingIndex
    ei = EmbeddingIndex()
    print(f'  Provider: {ei._provider}, Ready: {ei.is_ready}')
    if ei.is_ready:
        vec = ei.encode_single('贵州茅台RSI超卖支撑位')
        print(f'  Vector dim: {len(vec)}')
        vec2 = ei.encode_single('600519茅台关键价位')
        dot = sum(a * b for a, b in zip(vec, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        sim = dot / (norm1 * norm2) if norm1 > 0 and norm2 > 0 else 0
        print(f'  Semantic similarity: {sim:.3f}')
        assert sim > 0.3, f'Similarity too low: {sim}'
        print('  PASS (embedding active)')
    else:
        print('  SKIP (will use BM25 fallback)')


def test_bm25_search():
    print('\n[3/8] BM25 搜索测试...')
    from agent.memory import BM25Index
    bm25 = BM25Index()
    docs = [
        ('m1', '贵州茅台600519 RSI超卖 可能反弹', ['600519', 'RSI', '超卖']),
        ('m2', '平安银行000001 放量突破 均线金叉', ['000001', '放量', '突破']),
        ('m3', 'MACD金叉 低吸战法 缩量回调', ['MACD', '金叉', '低吸']),
        ('m4', '市场情绪低迷 冰点期 等待转机', ['市场情绪', '冰点']),
    ]
    bm25.build(docs)
    results = bm25.search('茅台RSI超卖', ['600519', 'RSI', '超卖'])
    print(f'  Query: 茅台RSI超卖 -> {[(r[0], round(r[1], 2)) for r in results]}')
    assert results[0][0] == 'm1', f'Expected m1, got {results[0][0]}'
    print('  PASS')


def test_memory_crud_and_search():
    print('\n[4/8] 记忆 CRUD + 搜索测试...')
    from agent.memory import MemoryManager
    mm = MemoryManager()
    mm.init_tables()

    memories_data = [
        ('贵州茅台600519在2000元有强支撑，RSI接近超卖区，可以低吸',
         'insight', '600519,贵州茅台,RSI,支撑位', '技术分析,白酒', 0.7),
        ('平安银行000001放量突破MA20，MACD金叉，短线看涨',
         'insight', '000001,平安银行,MACD,突破', '技术分析,银行', 0.6),
        ('用户偏好低吸操作，不喜欢追高',
         'preference', '低吸,交易风格', '用户偏好', 0.8),
        ('RSI超卖时要结合成交量看，不能单独使用',
         'learning', 'RSI,成交量', '用户教学', 0.8),
        ('市场情绪处于冰点期，应等待转机再入场',
         'fact', '市场情绪,冰点', '市场分析', 0.5),
    ]
    saved_ids = []
    for content, mtype, kw, tags, imp in memories_data:
        result = json.loads(mm.save_memory(content, mtype, kw, tags, imp))
        saved_ids.append(result['memory_id'])
        print(f'  Saved [{mtype}]: {result["memory_id"][:8]}... embedding={result.get("has_embedding", False)}')

    for query in ['茅台支撑位', 'MACD金叉突破', 'RSI使用技巧', '市场情绪']:
        result = json.loads(mm.search_memories(query))
        top = result['results'][0] if result['results'] else None
        method = result.get('search_method', '?')
        print(f'  Search "{query}": {result["total"]} results, method={method}')
        if top:
            print(f'    Top: [{top["memory_type"]}] {top["content"][:50]}... score={top.get("score", 0):.3f}')

    # Cleanup
    from src.database import get_connection
    conn = get_connection()
    conn.execute('DELETE FROM long_term_memories WHERE source_session_id IS NULL')
    conn.close()
    print('  PASS')


def test_prediction_tracker():
    print('\n[5/8] 预测跟踪测试...')
    from agent.memory import PredictionTracker
    pt = PredictionTracker()

    r1 = json.loads(pt.save_prediction(
        '600519', '贵州茅台', 'direction', 'bullish', 2100, 1900, 5, 'RSI超卖+支撑位', 0.7))
    r2 = json.loads(pt.save_prediction(
        '000001', '平安银行', 'direction', 'bearish', 0, 0, 3, '放量下跌', 0.6))
    print(f'  Saved: {r1["prediction_id"][:8]}..., {r2["prediction_id"][:8]}...')

    check = json.loads(pt.check_predictions('600519'))
    print(f'  Check 600519: {check["total"]} pending')

    verify = json.loads(pt.verify_prediction(r1['prediction_id'], 2150.0, 7.5))
    print(f'  Verify: outcome={verify["outcome"]}, return={verify["actual_return_pct"]}%')
    assert verify['outcome'] == 'correct', f'Expected correct, got {verify["outcome"]}'

    stats = json.loads(pt.get_accuracy_stats())
    print(f'  Stats: total={stats["total_verified"]}, correct={stats["correct"]}, accuracy={stats["accuracy"]}')

    # Cleanup
    from src.database import get_connection
    conn = get_connection()
    conn.execute('DELETE FROM predictions')
    conn.close()
    print('  PASS')


def test_user_profile():
    print('\n[6/8] 用户画像测试...')
    from agent.memory import UserProfile
    up = UserProfile()

    up.update_profile('trading_style', '低吸型', 0.6)
    up.update_profile('risk_tolerance', '稳健型', 0.5)
    up.update_profile('stop_loss_habit', '5%', 0.7)
    up.update_profile('watched_sectors', '白酒、银行', 0.5)

    profile = json.loads(up.get_profile())
    print(f'  Profile items: {profile["total"]}')
    for key, info in profile['profile'].items():
        print(f'    {key}: {info["value"]} (conf: {info["confidence"]:.0%})')

    summary = up.get_summary_text()
    print(f'  Summary: {summary[:80]}...')

    signals = up.extract_from_conversation([
        '我就喜欢做低吸，不喜欢追高',
        '止损一般设5%',
        '我主要做半导体板块',
    ])
    print(f'  Extracted signals: {len(signals)}')
    for s in signals:
        print(f'    {s["key"]}: {s["value"]} (conf: {s["confidence"]})')

    # Cleanup
    from src.database import get_connection
    conn = get_connection()
    conn.execute('DELETE FROM user_profile')
    conn.close()
    print('  PASS')


def test_knowledge_graph():
    print('\n[7/8] 知识图谱测试...')
    from agent.memory import KnowledgeGraph
    kg = KnowledgeGraph()

    e1 = kg.add_entity('indicator', 'RSI超卖')
    e2 = kg.add_entity('strategy', '低吸战法')
    e3 = kg.add_entity('indicator', 'MACD金叉')
    e4 = kg.add_entity('strategy', '放量突破战法')
    kg.add_relation(e1, e2, 'triggers', 0.8)
    kg.add_relation(e3, e4, 'triggers', 0.9)
    kg.add_relation(e1, e3, 'supports', 0.5)

    result = json.loads(kg.query_related('RSI'))
    print(f'  Relations for RSI: {result["total"]}')
    for r in result['results']:
        print(f'    {r["source"]["name"]} --[{r["relation"]}]--> {r["target"]["name"]} (w={r["weight"]})')

    entities, relations = kg.extract_from_memory('贵州茅台MACD金叉，RSI超卖反弹')
    print(f'  Extracted from memory: {len(entities)} entities')
    for ent in entities:
        print(f'    {ent["type"]}: {ent["name"]}')

    # Cleanup
    from src.database import get_connection
    conn = get_connection()
    conn.execute('DELETE FROM knowledge_entities')
    conn.execute('DELETE FROM knowledge_relations')
    conn.close()
    print('  PASS')


def test_context_injection():
    print('\n[8/8] 智能上下文注入测试...')
    from agent.memory import MemoryManager
    mm = MemoryManager()
    mm.init_tables()

    # Save some test memories first
    mm.save_memory('茅台RSI超卖', 'insight', '600519,RSI', 'test', 0.7)
    mm.save_memory('用户偏好低吸', 'learning', '低吸', 'test', 0.8,
                   learned_what='低吸为主', learned_why='风险低', apply_when='RSI超卖时')

    ctx = mm.get_context_block('茅台短线分析')
    print(f'  Context block: {len(ctx)} chars')
    sections = [line for line in ctx.split('\n') if line.startswith('###')]
    print(f'  Sections: {sections}')
    assert len(ctx) > 0, 'Context block is empty'

    # Cleanup
    from src.database import get_connection
    conn = get_connection()
    conn.execute('DELETE FROM long_term_memories WHERE source_session_id IS NULL')
    conn.close()
    print('  PASS')


if __name__ == '__main__':
    print('=' * 60)
    print('A股短线分析 AI Agent - 记忆系统升级测试')
    print('=' * 60)

    tests = [
        test_database_migration,
        test_embedding_index,
        test_bm25_search,
        test_memory_crud_and_search,
        test_prediction_tracker,
        test_user_profile,
        test_knowledge_graph,
        test_context_injection,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f'  FAIL: {e}')
            import traceback
            traceback.print_exc()
            failed += 1

    print('\n' + '=' * 60)
    print(f'RESULTS: {passed} passed, {failed} failed out of {len(tests)} tests')
    print('=' * 60)
    sys.exit(0 if failed == 0 else 1)
