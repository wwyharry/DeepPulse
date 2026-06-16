"""Embedding 向量索引，支持本地模型和 API 两种方式（懒加载）"""

import math
import struct


class EmbeddingIndex:
    """Embedding 向量索引，支持本地模型和 API 两种方式（懒加载）"""

    def __init__(self, setting: dict = None):
        self._model = None
        self._api_client = None
        self._cache = {}  # {memory_id: vector}
        self._provider = "none"
        self._ready = False
        self._setting = setting
        self._initialized = False
        # 不立即初始化，延迟到首次使用

    def _ensure_initialized(self):
        """确保已初始化（延迟加载）"""
        if not self._initialized:
            self._init_provider()
            self._initialized = True

    def _init_provider(self):
        """初始化 embedding 提供者"""
        emb_config = {}
        if self._setting:
            emb_config = self._setting.get("embedding", {})

        provider = emb_config.get("provider", "none")

        if provider == "local":
            self._init_local_model(emb_config.get("model", "shibing624/text2vec-base-chinese"))
        elif provider == "openai":
            self._init_api_client(emb_config)
        else:
            self._provider = "none"

    def _init_local_model(self, model_name: str):
        """初始化本地 sentence-transformers 模型"""
        try:
            import os

            # 国内默认使用 Hugging Face 镜像
            if "HF_ENDPOINT" not in os.environ:
                os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)
            self._provider = "local"
            self._ready = True
        except ImportError:
            pass
        except Exception:
            pass

    def _init_api_client(self, emb_config: dict):
        """初始化 embedding API 客户端"""
        try:
            from openai import OpenAI

            api_key = emb_config.get("api_key", "")
            base_url = emb_config.get("base_url", "")
            if api_key and base_url:
                self._api_client = OpenAI(api_key=api_key, base_url=base_url)
                self._provider = "openai"
                self._ready = True
        except Exception:
            pass

    @property
    def is_ready(self) -> bool:
        return self._ready

    def encode(self, texts: list[str]) -> list[list[float]]:
        """批量生成 embedding 向量（懒加载）"""
        # 延迟初始化
        self._ensure_initialized()

        if not self._ready:
            return [[] for _ in texts]

        try:
            if self._provider == "local":
                vectors = self._model.encode(texts, normalize_embeddings=True)
                return [v.tolist() for v in vectors]
            elif self._provider == "openai":
                resp = self._api_client.embeddings.create(
                    input=texts, model=self._setting.get("embedding", {}).get("model", "text-embedding-3-small")
                )
                return [d.embedding for d in resp.data]
        except Exception:
            pass
        return [[] for _ in texts]

    def encode_single(self, text: str) -> list[float]:
        """生成单条文本的 embedding（懒加载）"""
        # 延迟初始化
        self._ensure_initialized()

        result = self.encode([text])
        return result[0] if result else []

    def load_cache(self, conn) -> int:
        """从数据库加载所有 embedding 到内存缓存"""
        try:
            rows = conn.execute("""
                SELECT id, embedding FROM long_term_memories
                WHERE embedding IS NOT NULL AND is_archived = FALSE
            """).fetchall()
            self._cache = {}
            for row in rows:
                vec = self._bytes_to_vector(row[1])
                if vec:
                    self._cache[row[0]] = vec
            return len(self._cache)
        except Exception:
            return 0

    def add_to_cache(self, memory_id: str, vector: list[float]):
        """添加向量到缓存"""
        if vector:
            self._cache[memory_id] = vector

    def remove_from_cache(self, memory_id: str):
        """从缓存移除向量"""
        self._cache.pop(memory_id, None)

    def search(self, query_vector: list[float], top_k: int = 50) -> list[tuple[str, float]]:
        """向量相似度搜索，返回 (memory_id, similarity) 列表"""
        if not query_vector or not self._cache:
            return []

        results = []
        q_norm = self._norm(query_vector)
        if q_norm == 0:
            return []

        for mid, vec in self._cache.items():
            v_norm = self._norm(vec)
            if v_norm == 0:
                continue
            sim = self._dot(query_vector, vec) / (q_norm * v_norm)
            results.append((mid, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    @staticmethod
    def _dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=False))

    @staticmethod
    def _norm(a: list[float]) -> float:
        return math.sqrt(sum(x * x for x in a))

    @staticmethod
    def vector_to_bytes(vector: list[float]) -> bytes:
        """将向量转为 bytes 存储"""
        if not vector:
            return b""
        return struct.pack(f"{len(vector)}f", *vector)

    @staticmethod
    def _bytes_to_vector(data: bytes) -> list[float]:
        """将 bytes 还原为向量"""
        if not data:
            return []
        n = len(data) // 4
        return list(struct.unpack(f"{n}f", data))
