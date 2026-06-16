"""BM25 搜索索引，作为无 embedding 时的回退方案"""

import re


class BM25Index:
    """BM25 搜索索引，作为无 embedding 时的增强搜索方案"""

    def __init__(self):
        self._bm25 = None
        self._doc_ids = []
        self._docs = []
        self._ready = False

    def build(self, documents: list[tuple[str, str, list[str]]]):
        """构建 BM25 索引

        Args:
            documents: [(memory_id, content, keywords), ...]
        """
        try:
            from rank_bm25 import BM25Okapi

            self._doc_ids = []
            self._docs = []
            tokenized_corpus = []

            for mid, content, keywords in documents:
                tokens = self._tokenize(content, keywords)
                if tokens:
                    self._doc_ids.append(mid)
                    self._docs.append((content, keywords))
                    tokenized_corpus.append(tokens)

            if tokenized_corpus:
                self._bm25 = BM25Okapi(tokenized_corpus)
                self._ready = True
        except ImportError:
            self._ready = False
        except Exception:
            self._ready = False

    def search(self, query: str, query_keywords: list[str], top_k: int = 20) -> list[tuple[str, float]]:
        """BM25 搜索"""
        if not self._ready or not self._bm25:
            return []

        try:
            tokens = self._tokenize(query, query_keywords)
            if not tokens:
                return []

            scores = self._bm25.get_scores(tokens)
            results = [(self._doc_ids[i], float(scores[i])) for i in range(len(scores))]
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
        except Exception:
            return []

    def _tokenize(self, text: str, keywords: list[str] = None) -> list[str]:
        """中文分词（基于正则，不依赖分词库）"""
        tokens = set()
        # 提取技术术语
        tech_terms = [
            r"MA\d+",
            r"MACD",
            r"RSI",
            r"KDJ",
            r"BOLL",
            r"布林",
            r"金叉",
            r"死叉",
            r"超买",
            r"超卖",
            r"背离",
            r"突破",
            r"放量",
            r"缩量",
            r"涨停",
            r"跌停",
            r"支撑",
            r"压力",
        ]
        for pattern in tech_terms:
            matches = re.findall(pattern, text, re.IGNORECASE)
            tokens.update(m.lower() if m.isascii() else m for m in matches)

        # 提取股票代码
        codes = re.findall(r"\b[036]\d{5}\b", text)
        tokens.update(codes)

        # 提取中文片段（2-4字）
        cn_segments = re.findall(r"[一-鿿]{2,4}", text)
        tokens.update(cn_segments)

        # 加入关键词
        if keywords:
            tokens.update(keywords)

        return list(tokens)
