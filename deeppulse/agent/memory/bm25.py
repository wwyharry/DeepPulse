"""BM25 搜索索引，作为无 embedding 时的回退方案"""

import math
import re

from deeppulse.agent.memory.constants import STOCK_CODE_RE


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
                self._force_positive_idf()
                self._ready = True
        except ImportError:
            self._ready = False
        except Exception:
            self._ready = False

    def _force_positive_idf(self):
        """严格正 IDF：rank_bm25 的负 IDF 地板是 epsilon*average_idf，
        小语料/单主题库（词条命中半数以上文档）时平均 IDF 为负、地板也是负的，
        命中文档会被打成负分，叠加正分过滤后检索恒空。改为 ln(1+(N-df+0.5)/(df+0.5))。
        """
        bm25 = self._bm25
        n = bm25.corpus_size
        if n <= 0:
            return
        fixed = {}
        for word in list(bm25.idf.keys()):
            df = sum(1 for doc in bm25.doc_freqs if (doc.get(word) or 0) > 0)
            fixed[word] = math.log(1 + (n - df + 0.5) / (df + 0.5))
        bm25.idf = fixed
        bm25.average_idf = (sum(fixed.values()) / len(fixed)) if fixed else 0.0

    def search(self, query: str, query_keywords: list[str], top_k: int = 20) -> list[tuple[str, float]]:
        """BM25 搜索"""
        if not self._ready or not self._bm25:
            return []

        try:
            tokens = self._tokenize(query, query_keywords)
            if not tokens:
                return []

            scores = self._bm25.get_scores(tokens)
            # 过滤零分文档：与查询零重叠的文档不应作为命中返回
            # （正 IDF 化后命中文档必为正分，负分不再出现）
            results = [(self._doc_ids[i], float(scores[i])) for i in range(len(scores)) if scores[i] > 0]
            if not results:
                return []
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
        except Exception:
            return []

    def _tokenize(self, text: str, keywords: list[str] = None) -> list[str]:
        """中文分词（基于正则，不依赖分词库）

        中文用全量 2-gram：贪心 2-4 切块会让「贵州茅台」与查询「茅台」永不相交。
        2-gram 保证任意 ≥2 字子串可比；术语/代码/关键词仍整词入列。
        """
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

        # 提取股票代码（否定环视，覆盖沪深北交所，中文邻接不漏提）
        codes = re.findall(STOCK_CODE_RE, text)
        tokens.update(codes)

        # 中文全量 2-gram（连续中文串每相邻两字一粒；单字串不产 token）
        for run in re.findall(r"[一-鿿]+", text):
            if len(run) == 1:
                tokens.add(run)
            else:
                tokens.update(run[i : i + 2] for i in range(len(run) - 1))

        # 加入关键词
        if keywords:
            tokens.update(keywords)

        return list(tokens)
