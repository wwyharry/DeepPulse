"""EmbeddingIndex 缓存/维度/坏 blob 行为测试（此前只测了纯函数，缓存 API 零覆盖）"""

import struct

import pytest

from deeppulse.agent.memory import EmbeddingIndex


def _index() -> EmbeddingIndex:
    ei = EmbeddingIndex.__new__(EmbeddingIndex)
    ei._model = None
    ei._api_client = None
    ei._cache = {}
    ei._provider = "none"
    ei._ready = True
    ei._setting = None
    ei._initialized = True
    return ei


class TestBytesVector:
    def test_bad_length_raises(self):
        with pytest.raises(ValueError):
            EmbeddingIndex._bytes_to_vector(b"abc")

    def test_roundtrip(self):
        vec = [1.0, -0.5, 3.25]
        assert EmbeddingIndex._bytes_to_vector(EmbeddingIndex.vector_to_bytes(vec)) == pytest.approx(vec)

    def test_dot_dimension_mismatch_raises(self):
        """zip 按短边截断会掩盖换模型后的维度漂移 —— 必须抛错"""
        with pytest.raises(ValueError):
            EmbeddingIndex._dot([1, 0, 0, 0], [1, 0])


class TestCacheOps:
    def test_add_refuses_dimension_mismatch(self):
        ei = _index()
        assert ei.add_to_cache("a", [1.0, 0.0]) is True
        assert ei.add_to_cache("b", [1.0, 0.0, 0.0]) is False, "换模型后的异维向量不得混入缓存"
        assert "b" not in ei._cache

    def test_search_skips_mismatched_vectors(self):
        ei = _index()
        ei.add_to_cache("a", [1.0, 0.0])
        ei._cache["legacy"] = [1.0, 0.0, 0.0, 0.0]  # 手动混入异维（模拟旧缓存）
        results = ei.search([1.0, 0.0])
        assert [mid for mid, _ in results] == ["a"]

    def test_search_clamps_similarity(self):
        ei = _index()
        ei.add_to_cache("neg", [-1.0, 0.0])
        results = ei.search([1.0, 0.0])
        assert results and results[0][1] == 0.0, "负相似度不得以负分进入综合评分"

    def test_cache_cap_evicts_oldest(self, monkeypatch):
        from deeppulse import config

        monkeypatch.setattr(config, "EMBEDDING_CACHE_MAX", 2)
        ei = _index()
        ei.add_to_cache("a", [1.0])
        ei.add_to_cache("b", [2.0])
        ei.add_to_cache("c", [3.0])
        assert set(ei._cache) == {"b", "c"}, f"超限未淘汰: {set(ei._cache)}"

    def test_load_cache_skips_bad_blob(self):
        class FakeConn:
            def execute(self, sql, params=None):
                return self

            def fetchall(self):
                return [
                    ("good", struct.pack("2f", 1.0, 0.0)),
                    ("bad", b"abc"),  # 长度非 4 倍数
                ]

        ei = _index()
        n = ei.load_cache(FakeConn())
        assert n == 1 and set(ei._cache) == {"good"}, "单条坏 blob 不得拖垮整批加载"

    def test_encode_failure_returns_empty_not_fake(self):
        """encode 失败回落空向量（未知不得伪造向量）"""
        ei = _index()
        ei._ready = True
        ei._provider = "local"
        ei._model = None  # encode 会抛异常 → 兜底

        class Boom:
            def encode(self, texts, **kw):
                raise RuntimeError("model down")

        ei._model = Boom()
        assert ei.encode(["x"]) == [[]]
        assert ei.encode_single("x") == []
