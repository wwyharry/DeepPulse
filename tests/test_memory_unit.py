"""Unit tests for agent/memory.py — pure functions (no DB or network required)."""

import pytest

from deeppulse.agent.memory import BM25Index, EmbeddingIndex, KnowledgeGraph, MemoryManager, UserProfile

# ── EmbeddingIndex static methods ───────────────────────────────────


class TestEmbeddingIndex:
    def test_dot(self):
        assert EmbeddingIndex._dot([1, 2, 3], [4, 5, 6]) == pytest.approx(32)

    def test_dot_orthogonal(self):
        assert EmbeddingIndex._dot([1, 0], [0, 1]) == pytest.approx(0)

    def test_norm(self):
        assert EmbeddingIndex._norm([3, 4]) == pytest.approx(5.0)

    def test_norm_unit(self):
        assert EmbeddingIndex._norm([1, 0]) == pytest.approx(1.0)

    def test_vector_roundtrip(self):
        original = [1.0, 2.0, 3.0, -0.5, 100.0]
        as_bytes = EmbeddingIndex.vector_to_bytes(original)
        restored = EmbeddingIndex._bytes_to_vector(as_bytes)
        for a, b in zip(original, restored, strict=True):
            assert a == pytest.approx(b)

    def test_vector_to_bytes_empty(self):
        assert EmbeddingIndex.vector_to_bytes([]) == b""

    def test_bytes_to_vector_empty(self):
        assert EmbeddingIndex._bytes_to_vector(b"") == []


# ── BM25Index._tokenize ─────────────────────────────────────────────


class TestBM25Tokenize:
    def test_tech_terms(self):
        bm25 = BM25Index()
        tokens = bm25._tokenize("MACD金叉放量突破")
        # _tokenize lowercases ASCII matches, so "MACD" → "macd"
        assert "macd" in tokens
        assert "金叉" in tokens
        assert "放量" in tokens
        assert "突破" in tokens

    def test_stock_code(self):
        bm25 = BM25Index()
        tokens = bm25._tokenize("600519贵州茅台 RSI超卖", ["600519", "RSI"])
        assert "600519" in tokens
        assert "RSI" in tokens

    def test_chinese_segments(self):
        bm25 = BM25Index()
        tokens = bm25._tokenize("贵州茅台短线趋势")
        # Should extract 2-4 char Chinese segments
        assert any("茅台" in t for t in tokens)

    def test_empty_input(self):
        bm25 = BM25Index()
        tokens = bm25._tokenize("")
        assert tokens == []


# ── UserProfile.extract_from_conversation ───────────────────────────


class TestUserProfileExtract:
    def test_trading_style_low_buy(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["我就喜欢做低吸，不喜欢追高"])
        styles = [s for s in signals if s["key"] == "trading_style"]
        assert len(styles) == 1
        assert styles[0]["value"] == "低吸型"

    def test_trading_style_board_chasing(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["我主要做打板，追涨停"])
        styles = [s for s in signals if s["key"] == "trading_style"]
        assert styles[0]["value"] == "打板型"

    def test_risk_tolerance_aggressive(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["我比较激进，经常满仓干"])
        risk = [s for s in signals if s["key"] == "risk_tolerance"]
        assert risk[0]["value"] == "激进型"

    def test_stop_loss(self):
        up = UserProfile.__new__(UserProfile)
        # Regex expects "止损" followed by optional 设/为/用 then digits and %
        signals = up.extract_from_conversation(["止损设5%"])
        sl = [s for s in signals if s["key"] == "stop_loss_habit"]
        assert len(sl) == 1
        assert "5" in sl[0]["value"]

    def test_preferred_indicators(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation(["我主要看MACD和RSI"])
        ind = [s for s in signals if s["key"] == "preferred_indicators"]
        assert len(ind) == 1
        assert "MACD" in ind[0]["value"]
        assert "RSI" in ind[0]["value"]

    def test_empty_messages(self):
        up = UserProfile.__new__(UserProfile)
        signals = up.extract_from_conversation([])
        assert signals == []


# ── KnowledgeGraph.extract_from_memory ──────────────────────────────


class TestKnowledgeGraphExtract:
    def test_indicator_entities(self):
        kg = KnowledgeGraph.__new__(KnowledgeGraph)
        # extract_from_memory returns (entities, relations) tuple
        # Indicator patterns require specific formats like "MACD金叉" or "RSI<30"
        entities, relations = kg.extract_from_memory("MACD金叉，RSI<30超卖反弹")
        names = [e["name"] for e in entities]
        assert any("MACD" in n for n in names)
        assert any("RSI" in n for n in names)

    def test_stock_codes(self):
        kg = KnowledgeGraph.__new__(KnowledgeGraph)
        # Stock code regex \b([036]\d{5})\b needs word boundary; use space after code
        entities, relations = kg.extract_from_memory("600519 贵州茅台MACD金叉")
        stock_entities = [e for e in entities if e["type"] == "stock"]
        assert any(e["name"] == "600519" for e in stock_entities)


# ── MemoryManager.detect_learning_signals ───────────────────────────


class TestDetectLearningSignals:
    def test_correction(self):
        mm = MemoryManager.__new__(MemoryManager)
        signals = mm.detect_learning_signals("你分析错了，RSI不是这样用的")
        assert any(s["type"] == "纠错" for s in signals)

    def test_teaching(self):
        mm = MemoryManager.__new__(MemoryManager)
        signals = mm.detect_learning_signals("记住：RSI要结合量看")
        assert any(s["type"] == "教学" for s in signals)

    def test_self_correction(self):
        mm = MemoryManager.__new__(MemoryManager)
        signals = mm.detect_learning_signals(
            user_message="你觉得呢",
            agent_response="你说得对，我之前的判断有误",
        )
        assert any(s["type"] == "self_correction" for s in signals)

    def test_no_signal(self):
        mm = MemoryManager.__new__(MemoryManager)
        signals = mm.detect_learning_signals("帮我看看600519的走势")
        assert signals == []


# ── MemoryManager._extract_keywords ─────────────────────────────────


class TestExtractKeywords:
    def test_stock_code(self):
        mm = MemoryManager.__new__(MemoryManager)
        mm._TECH_TERMS = MemoryManager._TECH_TERMS
        # Note: stock code regex \b[036]\d{5}\b has boundary issues with Chinese chars,
        # so "600519" may not be extracted when directly followed by Chinese text.
        # Test with space-separated code to verify regex works in isolation.
        keywords = mm._extract_keywords("600519 贵州茅台MACD金叉")
        assert "600519" in keywords
        assert "MACD" in keywords
        assert "金叉" in keywords

    def test_tech_terms(self):
        mm = MemoryManager.__new__(MemoryManager)
        mm._TECH_TERMS = MemoryManager._TECH_TERMS
        keywords = mm._extract_keywords("MACD金叉放量突破超卖反弹")
        assert "MACD" in keywords
        assert "金叉" in keywords
        assert "放量" in keywords
        assert "突破" in keywords

    def test_empty(self):
        mm = MemoryManager.__new__(MemoryManager)
        mm._TECH_TERMS = MemoryManager._TECH_TERMS
        keywords = mm._extract_keywords("")
        assert keywords == []
