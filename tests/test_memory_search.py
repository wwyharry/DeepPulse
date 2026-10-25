"""记忆检索链回归 — BM25 缓存污染/负 IDF/关键词回退/幽灵向量/去重/诚实回执

全部走 tmp 隔离库。每个用例钉一个曾被 84-agent 审计确认的缺陷，防回退。
"""

import json

import pytest

from deeppulse.agent.memory import MemoryManager
from deeppulse.agent.memory.bm25 import BM25Index
from deeppulse.agent.memory.manager import _clip


def _mm(tmp_path) -> MemoryManager:
    mm = MemoryManager(db_path=tmp_path / "search.duckdb")
    mm.init_tables()
    return mm


class FakeEmbedding:
    """可控 embedding 桩：可设定返回向量/抛异常/拒答"""

    def __init__(self, vec=None, raise_error=False):
        self.is_ready = True
        self.vec = vec if vec is not None else [1.0, 0.0, 0.0]
        self.raise_error = raise_error
        self.cache = {}
        self.encode_calls = 0

    def encode_single(self, text):
        self.encode_calls += 1
        if self.raise_error:
            raise RuntimeError("encode boom")
        return list(self.vec)

    def search(self, query_vector, top_k=50):
        q = query_vector
        out = []
        for mid, v in self.cache.items():
            if len(v) != len(q):
                continue
            num = sum(a * b for a, b in zip(q, v, strict=True))
            nq = sum(a * a for a in q) ** 0.5
            nv = sum(b * b for b in v) ** 0.5
            if nq and nv:
                out.append((mid, min(1.0, max(0.0, num / (nq * nv)))))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:top_k]

    def add_to_cache(self, mid, vec):
        self.cache[mid] = list(vec)

    def remove_from_cache(self, mid):
        self.cache.pop(mid, None)


class TestBM25Fixes:
    def test_negative_idf_small_corpus_still_finds(self):
        """负 IDF 回归：单主题小语料（词条命中全部文档）检索不得恒空"""
        bm25 = BM25Index()
        docs = [(f"m{i}", f"贵州茅台观点{i}: 量价配合良好", ["600519", "茅台"]) for i in range(6)]
        bm25.build(docs)
        results = bm25.search("茅台量价", ["茅台"])
        assert len(results) >= 6, f"同主题全命中不该被负分过滤掉: {results}"

    def test_single_doc_self_query(self):
        bm25 = BM25Index()
        bm25.build([("m1", "布林带收口预示变盘", ["布林带"])])
        results = bm25.search("布林带收口", ["布林带"])
        assert results and results[0][0] == "m1"

    def test_token_2gram_substring_match(self):
        """贪心 2-4 切块会让「贵州茅台」与查询「茅台」永不相交 —— 2-gram 保证可比"""
        bm25 = BM25Index()
        tokens = bm25._tokenize("贵州茅台业绩优秀")
        assert "茅台" in tokens
        assert "贵州" in tokens

    def test_stock_code_cjk_adjacent_and_bse(self):
        """代码提取：中文邻接不漏提；北交所 4/8/920 字头必须识别"""
        bm25 = BM25Index()
        for text, code in [
            ("贵州茅台600519看涨", "600519"),
            ("分析600519贵州茅台", "600519"),
            ("关注835185的支撑", "835185"),
            ("430047今日复牌", "430047"),
            ("920001新股观察", "920001"),
        ]:
            tokens = bm25._tokenize(text)
            assert code in tokens, f"{text!r} 未提出 {code}"


class TestSearchChain:
    def test_bm25_index_not_polluted_by_type_filter(self, tmp_path):
        """缓存索引不得带走首次搜索的 memory_type 过滤（回归：类型搜索后全量搜索漏其他类型）"""
        mm = _mm(tmp_path)
        mm.save_memory("均线多头排列是趋势信号", "learning", "均线", "t", 0.5)
        mm.save_memory("均线支撑有效，观察量能", "insight", "均线", "t", 0.5)

        r1 = json.loads(mm.search_memories("均线", memory_type="learning"))
        assert r1["total"] == 1
        assert all(m["memory_type"] == "learning" for m in r1["results"])

        # 关键：紧接着的无过滤搜索必须看到另一条 insight（旧缓存会只搜 learning 子集）
        r2 = json.loads(mm.search_memories("均线"))
        assert r2["total"] == 2, f"类型过滤污染了索引缓存: {r2}"

    def test_keyword_fallback_matches_content(self, tmp_path):
        """关键词回退必须查 content：正文完全匹配却搜不到是致命漏召回"""
        mm = _mm(tmp_path)
        mm.save_memory("预示变盘的信号来自布林带收口", "insight", "技术面", "t", 0.5)
        r = json.loads(mm.search_memories("预示变盘"))
        assert r["total"] >= 1, f"content 命中却被漏: {r}"

    def test_search_method_reports_actual_path(self, tmp_path):
        """search_method 如实上报实际执行路径：embedding 失败降级时不得虚报 embedding"""
        mm = _mm(tmp_path)
        mm._embedding_index = FakeEmbedding(raise_error=True)
        mm.save_memory("量价配合判断趋势", "insight", "量价", "t", 0.5)
        r = json.loads(mm.search_memories("量价"))
        assert r["search_method"] in ("bm25", "keyword_fallback"), r

    def test_access_count_reflected_in_response(self, tmp_path):
        mm = _mm(tmp_path)
        mm.save_memory("低吸策略在冰点期更稳", "insight", "低吸", "t", 0.5)
        r = json.loads(mm.search_memories("低吸"))
        assert r["results"][0]["access_count"] >= 1, "命中回执的访问计数应已自增"

    def test_top_k_negative_does_not_crash(self, tmp_path):
        mm = _mm(tmp_path)
        mm.save_memory("内容A", "insight", "A", "t", 0.5)
        r = json.loads(mm.search_memories("内容", top_k=-1))
        assert r["total"] >= 1  # 夹取到 1，不是负切片也不是崩

    def test_list_total_is_db_count_not_returned(self, tmp_path):
        mm = _mm(tmp_path)
        for i in range(5):
            mm.save_memory(f"列表内容{i}", "insight", "列表", "t", 0.5)
        r = json.loads(mm.list_memories(limit=2))
        assert r["returned"] == 2
        assert r["total"] == 5, "total 必须是库内总数，不得拿返回条数冒充"

    def test_corrupt_keywords_json_does_not_kill_search(self, tmp_path):
        """单行 keywords_json 损坏不得让整个 search/list 抛 JSONDecodeError"""
        mm = _mm(tmp_path)
        saved = json.loads(mm.save_memory("损坏行关键词测试", "insight", "完好", "t", 0.5))
        conn = mm._conn()
        conn.execute(
            "UPDATE long_term_memories SET keywords_json = '{oops' WHERE id = ?", [saved["memory_id"]]
        )
        conn.close()

        r = json.loads(mm.search_memories("损坏"))
        assert r["total"] >= 1
        lst = json.loads(mm.list_memories())
        assert lst["returned"] >= 1

    def test_stock_code_searchable_after_cjk_adjacent_save(self, tmp_path):
        """端到端：贵州茅台600519 看涨 → 按 600519 搜索必须命中（旧 \\b 提取漏码）"""
        mm = _mm(tmp_path)
        mm.save_memory("贵州茅台600519看涨,支撑位1700", "insight", "", "t", 0.5)
        r = json.loads(mm.search_memories("600519"))
        assert r["total"] >= 1, f"代码没进关键词导致搜不到: {r}"


class TestCrudHonesty:
    def test_save_dedup_returns_existing(self, tmp_path):
        mm = _mm(tmp_path)
        r1 = json.loads(mm.save_memory("用户偏好低吸，不喜欢追高", "preference", "", "", 0.5))
        r2 = json.loads(mm.save_memory("用户偏好低吸，不喜欢追高", "preference", "", "", 0.8))
        assert r2["status"] == "existing"
        assert r2["memory_id"] == r1["memory_id"]
        lst = json.loads(mm.list_memories())
        assert lst["total"] == 1, "相同内容不得重复堆积"
        # 去重时取更高重要度
        assert lst["results"][0]["importance"] == pytest.approx(0.8)

    def test_delete_nonexistent_is_error(self, tmp_path):
        mm = _mm(tmp_path)
        r = json.loads(mm.delete_memory("does-not-exist"))
        assert "error" in r, "不存在的 id 不得谎报 archived 成功"

    def test_update_nonexistent_is_error_and_noop(self, tmp_path):
        mm = _mm(tmp_path)
        r = json.loads(mm.update_memory("does-not-exist", content="x"))
        assert "error" in r
        saved = json.loads(mm.save_memory("原始内容", "insight", "", "", 0.5))
        r = json.loads(mm.update_memory(saved["memory_id"]))
        assert r["status"] == "noop", "无字段变更不得谎报 updated"

    def test_update_preserves_manual_keywords(self, tmp_path):
        """content 变更时人工关键词取并集保留，不得被 _extract_keywords 整体覆盖"""
        mm = _mm(tmp_path)
        saved = json.loads(mm.save_memory("贵州茅台600519 看涨 支撑1700", "insight", "600519,贵州茅台,支撑位", "", 0.5))
        mm.update_memory(saved["memory_id"], content="贵州茅台600519 看涨 维持判断")
        lst = json.loads(mm.list_memories())
        kw = lst["results"][0]["keywords"]
        assert "600519" in kw and "贵州茅台" in kw and "支撑位" in kw, f"人工关键词丢失: {kw}"

    def test_update_tags_empty_clears_and_none_keeps(self, tmp_path):
        mm = _mm(tmp_path)
        saved = json.loads(mm.save_memory("标签语义测试", "insight", "标签", "保留我", 0.5))
        mm.update_memory(saved["memory_id"], content="标签语义测试2")  # tags=None 保持
        lst = json.loads(mm.list_memories())
        assert "保留我" in lst["results"][0]["tags"]
        mm.update_memory(saved["memory_id"], tags="")  # 空串清空
        lst = json.loads(mm.list_memories())
        assert lst["results"][0]["tags"] == []

    def test_update_content_clears_ghost_embedding(self, tmp_path):
        """正文改写后旧向量必须退场（encode 失败也不得留幽灵向量按旧内容被召回）"""
        mm = _mm(tmp_path)
        fake = FakeEmbedding(vec=[1.0, 0.0, 0.0])
        mm._embedding_index = fake
        saved = json.loads(mm.save_memory("旧内容AAA", "insight", "旧", "", 0.5))
        assert fake.cache.get(saved["memory_id"]) == [1.0, 0.0, 0.0]

        fake.raise_error = True
        mm.update_memory(saved["memory_id"], content="新内容BBB")
        assert saved["memory_id"] not in fake.cache, "encode 失败后幽灵向量仍在缓存"
        conn = mm._conn()
        emb = conn.execute(
            "SELECT embedding FROM long_term_memories WHERE id = ?", [saved["memory_id"]]
        ).fetchone()[0]
        conn.close()
        assert emb is None, "库内 embedding 列未清理"

    def test_importance_clamp_flag(self, tmp_path):
        mm = _mm(tmp_path)
        r = json.loads(mm.save_memory("越界重要度", "insight", "", "", 5.0))
        assert r.get("importance_clamped") is True
        lst = json.loads(mm.list_memories())
        assert 0.0 <= lst["results"][0]["importance"] <= 1.0

    def test_unknown_memory_type_rejected(self, tmp_path):
        mm = _mm(tmp_path)
        r = json.loads(mm.save_memory("神秘类型", "mystery", "", "", 0.5))
        assert "error" in r


class TestClip:
    def test_clip_appends_ellipsis(self):
        assert _clip("一二三四五", 3).endswith("…")
        assert len(_clip("一二三四五", 3)) <= 4

    def test_clip_never_cuts_digits_mid_number(self):
        """价位 2000 不得被截成 20 —— 伪造数字违反数据诚实"""
        import re

        text = "支撑价是2000元压力位3000元" + "的更多说明" * 10
        for budget in range(1, len(text)):
            out = _clip(text, budget)
            for num in re.findall(r"\d+", out.rstrip("…")):
                assert num in text, f"预算 {budget} 截断伪造了数字 {num}: {out!r}"

    def test_clip_short_text_unchanged(self):
        assert _clip("短文本", 100) == "短文本"
        assert _clip(None, 10) == ""
