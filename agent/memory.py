"""Agent 记忆系统 - 长期记忆管理、语义检索、预测跟踪、用户画像、知识图谱"""
import json
import uuid
import re
import math
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path

import config
from src.database import get_connection, init_memory_tables


# 各记忆类型的默认衰减半衰期（天）
DEFAULT_HALF_LIFE = {
    "preference": 90,
    "insight": 30,
    "fact": 60,
    "context": 14,
    "summary": 45,
    "learning": 180,
}


# ═══════════════════════════════════════════════════════════════════
# Embedding 向量索引
# ═══════════════════════════════════════════════════════════════════

class EmbeddingIndex:
    """Embedding 向量索引，支持本地模型和 API 两种方式"""

    def __init__(self, setting: dict = None):
        self._model = None
        self._api_client = None
        self._cache = {}  # {memory_id: vector}
        self._provider = "none"
        self._ready = False
        self._setting = setting
        self._init_provider()

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
            if 'HF_ENDPOINT' not in os.environ:
                os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
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
        """批量生成 embedding 向量"""
        if not self._ready:
            return [[] for _ in texts]

        try:
            if self._provider == "local":
                vectors = self._model.encode(texts, normalize_embeddings=True)
                return [v.tolist() for v in vectors]
            elif self._provider == "openai":
                resp = self._api_client.embeddings.create(
                    input=texts,
                    model=self._setting.get("embedding", {}).get("model", "text-embedding-3-small")
                )
                return [d.embedding for d in resp.data]
        except Exception:
            pass
        return [[] for _ in texts]

    def encode_single(self, text: str) -> list[float]:
        """生成单条文本的 embedding"""
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
        return sum(x * y for x, y in zip(a, b))

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


# ═══════════════════════════════════════════════════════════════════
# BM25 搜索（无 embedding 时的回退方案）
# ═══════════════════════════════════════════════════════════════════

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
            r'MA\d+', r'MACD', r'RSI', r'KDJ', r'BOLL', r'布林',
            r'金叉', r'死叉', r'超买', r'超卖', r'背离', r'突破',
            r'放量', r'缩量', r'涨停', r'跌停', r'支撑', r'压力',
        ]
        for pattern in tech_terms:
            matches = re.findall(pattern, text, re.IGNORECASE)
            tokens.update(m.lower() if m.isascii() else m for m in matches)

        # 提取股票代码
        codes = re.findall(r'\b[036]\d{5}\b', text)
        tokens.update(codes)

        # 提取中文片段（2-4字）
        cn_segments = re.findall(r'[一-鿿]{2,4}', text)
        tokens.update(cn_segments)

        # 加入关键词
        if keywords:
            tokens.update(keywords)

        return list(tokens)


# ═══════════════════════════════════════════════════════════════════
# 预测跟踪系统
# ═══════════════════════════════════════════════════════════════════

class PredictionTracker:
    """预测跟踪与结果反馈"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    def _conn(self):
        return get_connection(self.db_path)

    def save_prediction(self, stock_code: str, stock_name: str = "",
                        prediction_type: str = "direction",
                        direction: str = "neutral",
                        target_price: float = 0, stop_loss: float = 0,
                        timeframe_days: int = 5, reasoning: str = "",
                        confidence: float = 0.5,
                        memory_ids: list[str] = None) -> str:
        """保存一条预测"""
        pred_id = str(uuid.uuid4())
        now = datetime.now()
        check_date = now + timedelta(days=timeframe_days)

        conn = self._conn()
        try:
            conn.execute("""
                INSERT INTO predictions
                (id, stock_code, stock_name, prediction_type, direction,
                 target_price, stop_loss, timeframe_days, reasoning, confidence,
                 memory_ids_json, created_at, check_after_date, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, [pred_id, stock_code, stock_name, prediction_type, direction,
                  target_price, stop_loss, timeframe_days, reasoning, confidence,
                  json.dumps(memory_ids or [], ensure_ascii=False),
                  now, check_date])
            return json.dumps({
                "status": "saved",
                "prediction_id": pred_id,
                "stock_code": stock_code,
                "direction": direction,
                "check_after": str(check_date.date()),
            }, ensure_ascii=False)
        finally:
            conn.close()

    def check_predictions(self, stock_code: str = None,
                          current_price: float = None) -> str:
        """检查预测结果。提供 stock_code 时检查该股票的预测，否则检查所有到期预测。"""
        conn = self._conn()
        try:
            if stock_code:
                rows = conn.execute("""
                    SELECT id, stock_code, stock_name, direction, target_price,
                           stop_loss, timeframe_days, reasoning, confidence,
                           memory_ids_json, created_at, outcome
                    FROM predictions
                    WHERE stock_code = ? AND outcome = 'pending'
                    ORDER BY created_at DESC
                """, [stock_code]).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, stock_code, stock_name, direction, target_price,
                           stop_loss, timeframe_days, reasoning, confidence,
                           memory_ids_json, created_at, outcome
                    FROM predictions
                    WHERE outcome = 'pending' AND check_after_date <= current_date
                    ORDER BY created_at DESC
                    LIMIT 20
                """).fetchall()

            if not rows:
                return json.dumps({"message": "没有待验证的预测", "checked": 0}, ensure_ascii=False)

            results = []
            for row in rows:
                pred = {
                    "id": row[0], "stock_code": row[1], "stock_name": row[2],
                    "direction": row[3], "target_price": row[4],
                    "stop_loss": row[5], "timeframe_days": row[6],
                    "reasoning": row[7][:100] if row[7] else "",
                    "confidence": row[8], "created_at": str(row[10]),
                    "days_elapsed": (datetime.now() - row[10]).days,
                }
                results.append(pred)

            return json.dumps({
                "pending_predictions": results,
                "total": len(results),
                "message": f"找到 {len(results)} 个待验证预测，请用 verify_prediction 验证结果",
            }, ensure_ascii=False)
        finally:
            conn.close()

    def verify_prediction(self, prediction_id: str,
                          actual_price: float,
                          actual_return_pct: float = None) -> str:
        """验证单个预测结果"""
        conn = self._conn()
        try:
            row = conn.execute("""
                SELECT direction, target_price, stop_loss, created_at, stock_code
                FROM predictions WHERE id = ?
            """, [prediction_id]).fetchone()

            if not row:
                return json.dumps({"error": f"预测 {prediction_id} 不存在"}, ensure_ascii=False)

            direction, target_price, stop_loss, created_at, stock_code = row
            days_held = (datetime.now() - created_at).days

            # 判断结果
            if actual_return_pct is None and target_price and target_price > 0:
                # 用目标价估算收益
                actual_return_pct = 0  # 需要外部提供

            if direction == "bullish":
                if actual_return_pct and actual_return_pct > 3:
                    outcome = "correct"
                elif actual_return_pct and actual_return_pct < -3:
                    outcome = "wrong"
                else:
                    outcome = "partial"
            elif direction == "bearish":
                if actual_return_pct and actual_return_pct < -3:
                    outcome = "correct"
                elif actual_return_pct and actual_return_pct > 3:
                    outcome = "wrong"
                else:
                    outcome = "partial"
            else:
                outcome = "partial"

            now = datetime.now()
            conn.execute("""
                UPDATE predictions
                SET actual_price = ?, actual_return_pct = ?, outcome = ?,
                    checked_at = ?
                WHERE id = ?
            """, [actual_price, actual_return_pct, outcome, now, prediction_id])

            # 更新关联记忆的重要性
            memory_ids_json = conn.execute(
                "SELECT memory_ids_json FROM predictions WHERE id = ?",
                [prediction_id]
            ).fetchone()[0]
            memory_ids = json.loads(memory_ids_json) if memory_ids_json else []

            if memory_ids and outcome in ("correct", "wrong"):
                adjustment = 0.1 if outcome == "correct" else -0.1
                for mid in memory_ids:
                    conn.execute("""
                        UPDATE long_term_memories
                        SET importance = MIN(1.0, MAX(0.0, importance + ?)),
                            updated_at = ?
                        WHERE id = ?
                    """, [adjustment, now, mid])

            return json.dumps({
                "prediction_id": prediction_id,
                "stock_code": stock_code,
                "direction": direction,
                "actual_return_pct": actual_return_pct,
                "outcome": outcome,
                "days_held": days_held,
                "memory_adjusted": len(memory_ids),
            }, ensure_ascii=False)
        finally:
            conn.close()

    def get_accuracy_stats(self) -> str:
        """获取预测准确率统计"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT outcome, COUNT(*)
                FROM predictions
                WHERE outcome != 'pending'
                GROUP BY outcome
            """).fetchall()

            stats = {row[0]: row[1] for row in rows}
            total = sum(stats.values())
            correct = stats.get("correct", 0)
            wrong = stats.get("wrong", 0)
            partial = stats.get("partial", 0)

            # 按方向统计
            dir_rows = conn.execute("""
                SELECT direction, outcome, COUNT(*)
                FROM predictions
                WHERE outcome != 'pending'
                GROUP BY direction, outcome
            """).fetchall()

            by_direction = {}
            for d, o, c in dir_rows:
                if d not in by_direction:
                    by_direction[d] = {}
                by_direction[d][o] = c

            return json.dumps({
                "total_verified": total,
                "correct": correct,
                "wrong": wrong,
                "partial": partial,
                "accuracy": f"{correct/total*100:.1f}%" if total > 0 else "N/A",
                "by_direction": by_direction,
            }, ensure_ascii=False)
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════
# 用户画像
# ═══════════════════════════════════════════════════════════════════

class UserProfile:
    """用户交易画像管理"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    def _conn(self):
        return get_connection(self.db_path)

    def get_profile(self) -> str:
        """获取完整用户画像"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT key, value, confidence, updated_at
                FROM user_profile
                ORDER BY confidence DESC
            """).fetchall()

            profile = {}
            for row in rows:
                profile[row[0]] = {
                    "value": row[1],
                    "confidence": row[2],
                    "updated_at": str(row[3]) if row[3] else None,
                }

            return json.dumps({"profile": profile, "total": len(profile)},
                              ensure_ascii=False)
        finally:
            conn.close()

    def update_profile(self, key: str, value: str,
                       confidence: float = 0.5,
                       source_memory_ids: list[str] = None) -> str:
        """更新用户画像"""
        conn = self._conn()
        try:
            now = datetime.now()
            # 检查是否已存在
            existing = conn.execute(
                "SELECT confidence FROM user_profile WHERE key = ?", [key]
            ).fetchone()

            if existing:
                # 合并置信度：取加权平均
                old_conf = existing[0]
                new_conf = min(1.0, (old_conf + confidence) / 2 + 0.05)
                conn.execute("""
                    UPDATE user_profile
                    SET value = ?, confidence = ?, source_memory_ids = ?, updated_at = ?
                    WHERE key = ?
                """, [value, new_conf,
                      json.dumps(source_memory_ids or [], ensure_ascii=False),
                      now, key])
            else:
                conn.execute("""
                    INSERT INTO user_profile (key, value, confidence, source_memory_ids, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, [key, value, confidence,
                      json.dumps(source_memory_ids or [], ensure_ascii=False),
                      now])

            return json.dumps({"status": "updated", "key": key, "value": value,
                               "confidence": confidence}, ensure_ascii=False)
        finally:
            conn.close()

    def get_summary_text(self, max_chars: int = 500) -> str:
        """获取用户画像的文本摘要，用于注入 system prompt"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT key, value, confidence
                FROM user_profile
                WHERE confidence >= 0.3
                ORDER BY confidence DESC
            """).fetchall()

            if not rows:
                return ""

            labels = {
                "trading_style": "交易风格",
                "risk_tolerance": "风险偏好",
                "preferred_indicators": "偏好指标",
                "watched_sectors": "关注板块",
                "watched_stocks": "关注股票",
                "position_sizing": "仓位习惯",
                "profit_target": "止盈目标",
                "stop_loss_habit": "止损习惯",
                "trading_frequency": "交易频率",
            }

            lines = []
            total = 0
            for row in rows:
                label = labels.get(row[0], row[0])
                conf_str = f"(置信度:{row[2]:.0%})"
                line = f"- {label}: {row[1]} {conf_str}"
                if total + len(line) > max_chars:
                    break
                lines.append(line)
                total += len(line) + 1

            return "\n".join(lines)
        finally:
            conn.close()

    def extract_from_conversation(self, user_messages: list[str]) -> list[dict]:
        """从用户消息中提取画像信号（规则层）"""
        signals = []
        combined = " ".join(user_messages)

        # 交易风格
        if re.search(r'低吸|抄底|回调买入', combined):
            signals.append({"key": "trading_style", "value": "低吸型", "confidence": 0.6})
        elif re.search(r'打板|追涨停|追高', combined):
            signals.append({"key": "trading_style", "value": "打板型", "confidence": 0.6})
        elif re.search(r'半路|点火|启动', combined):
            signals.append({"key": "trading_style", "value": "半路型", "confidence": 0.6})
        elif re.search(r'接力|二板|三板', combined):
            signals.append({"key": "trading_style", "value": "接力型", "confidence": 0.6})

        # 风险偏好
        if re.search(r'保守|稳健|风险低', combined):
            signals.append({"key": "risk_tolerance", "value": "保守型", "confidence": 0.5})
        elif re.search(r'激进|高风险|满仓', combined):
            signals.append({"key": "risk_tolerance", "value": "激进型", "confidence": 0.5})

        # 止损习惯
        stop_match = re.search(r'止损[设为用]*(\d+(?:\.\d+)?)\s*%', combined)
        if stop_match:
            signals.append({"key": "stop_loss_habit",
                           "value": f"{stop_match.group(1)}%",
                           "confidence": 0.7})

        # 偏好指标
        indicators = []
        for ind in ["MACD", "RSI", "KDJ", "布林", "均线", "量价"]:
            if ind in combined:
                indicators.append(ind)
        if indicators:
            signals.append({"key": "preferred_indicators",
                           "value": "、".join(indicators),
                           "confidence": 0.4})

        # 关注板块
        sector_match = re.findall(r'(?:关注|做|看好|研究)\s*([一-鿿]{2,6}(?:板块|行业|概念|股))', combined)
        if sector_match:
            signals.append({"key": "watched_sectors",
                           "value": "、".join(sector_match[:3]),
                           "confidence": 0.5})

        return signals


# ═══════════════════════════════════════════════════════════════════
# 知识图谱
# ═══════════════════════════════════════════════════════════════════

class KnowledgeGraph:
    """结构化交易知识图谱"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    def _conn(self):
        return get_connection(self.db_path)

    def add_entity(self, entity_type: str, name: str,
                   attributes: dict = None) -> str:
        """添加或更新知识实体"""
        entity_id = str(uuid.uuid4())
        now = datetime.now()
        conn = self._conn()
        try:
            # 检查是否已存在同名同类型实体
            existing = conn.execute("""
                SELECT id FROM knowledge_entities
                WHERE entity_type = ? AND name = ?
            """, [entity_type, name]).fetchone()

            if existing:
                conn.execute("""
                    UPDATE knowledge_entities
                    SET attributes_json = ?, updated_at = ?
                    WHERE id = ?
                """, [json.dumps(attributes or {}, ensure_ascii=False), now, existing[0]])
                return existing[0]

            conn.execute("""
                INSERT INTO knowledge_entities (id, entity_type, name, attributes_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [entity_id, entity_type, name,
                  json.dumps(attributes or {}, ensure_ascii=False), now, now])
            return entity_id
        finally:
            conn.close()

    def add_relation(self, source_id: str, target_id: str,
                     relation_type: str, weight: float = 1.0,
                     evidence_memory_ids: list[str] = None) -> str:
        """添加知识关系"""
        rel_id = str(uuid.uuid4())
        conn = self._conn()
        try:
            # 检查是否已存在相同关系
            existing = conn.execute("""
                SELECT id, weight FROM knowledge_relations
                WHERE source_id = ? AND target_id = ? AND relation_type = ?
            """, [source_id, target_id, relation_type]).fetchone()

            if existing:
                # 更新权重（取平均）
                new_weight = (existing[1] + weight) / 2
                conn.execute("""
                    UPDATE knowledge_relations SET weight = ? WHERE id = ?
                """, [new_weight, existing[0]])
                return existing[0]

            conn.execute("""
                INSERT INTO knowledge_relations
                (id, source_id, target_id, relation_type, weight, evidence_memory_ids, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [rel_id, source_id, target_id, relation_type, weight,
                  json.dumps(evidence_memory_ids or [], ensure_ascii=False),
                  datetime.now()])
            return rel_id
        finally:
            conn.close()

    def query_related(self, entity_name: str = None,
                      entity_type: str = None,
                      relation_type: str = None) -> str:
                        """查询相关的知识实体和关系"""
                        conn = self._conn()
                        try:
                            conditions = []
                            params = []

                            if entity_name:
                                conditions.append("(e1.name LIKE ? OR e2.name LIKE ?)")
                                params.extend([f"%{entity_name}%", f"%{entity_name}%"])
                            if entity_type:
                                conditions.append("(e1.entity_type = ? OR e2.entity_type = ?)")
                                params.extend([entity_type, entity_type])
                            if relation_type:
                                conditions.append("r.relation_type = ?")
                                params.append(relation_type)

                            where = " AND ".join(conditions) if conditions else "1=1"
                            sql = f"""
                                SELECT e1.name, e1.entity_type, r.relation_type,
                                       e2.name, e2.entity_type, r.weight
                                FROM knowledge_relations r
                                JOIN knowledge_entities e1 ON r.source_id = e1.id
                                JOIN knowledge_entities e2 ON r.target_id = e2.id
                                WHERE {where}
                                ORDER BY r.weight DESC
                                LIMIT 20
                            """
                            rows = conn.execute(sql, params).fetchall()

                            results = []
                            for row in rows:
                                results.append({
                                    "source": {"name": row[0], "type": row[1]},
                                    "relation": row[2],
                                    "target": {"name": row[3], "type": row[4]},
                                    "weight": row[5],
                                })

                            return json.dumps({"results": results, "total": len(results)},
                                              ensure_ascii=False)
                        finally:
                            conn.close()

    def extract_from_memory(self, content: str, memory_type: str = "") -> list[dict]:
        """从记忆内容中提取实体和关系"""
        entities = []
        relations = []

        # 提取技术指标实体
        indicator_patterns = {
            r'RSI[<>]=?\s*\d+': 'indicator',
            r'MACD\s*金叉': 'indicator',
            r'MACD\s*死叉': 'indicator',
            r'KDJ\s*金叉': 'indicator',
            r'KDJ\s*死叉': 'indicator',
            r'MA\d+[<>]MA\d+': 'indicator',
            r'放量': 'indicator',
            r'缩量': 'indicator',
        }
        for pattern, etype in indicator_patterns.items():
            matches = re.findall(pattern, content)
            for m in matches:
                entities.append({"type": etype, "name": m.strip()})

        # 提取股票代码实体
        codes = re.findall(r'\b([036]\d{5})\b', content)
        for code in codes:
            entities.append({"type": "stock", "name": code})

        # 提取战法实体
        strategy_names = re.findall(r'([一-鿿]{2,8}战法)', content)
        for sn in strategy_names:
            entities.append({"type": "strategy", "name": sn})

        # 提取规则关系
        if memory_type == "learning":
            # 学习记忆中的 "X 应用于 Y" 关系
            apply_matches = re.findall(r'([一-鿿\w]+)\s*(?:适用于|应用于|触发)', content)
            for m in apply_matches:
                relations.append({"source": m, "target": "user_rule", "type": "supports"})

        return entities, relations


# ═══════════════════════════════════════════════════════════════════
# 记忆管理器（核心，整合所有子系统）
# ═══════════════════════════════════════════════════════════════════

class MemoryManager:
    """Agent 记忆管理器

    功能：
    - 长期记忆的 CRUD（save/search/update/delete/list）
    - 语义检索（embedding + BM25 回退）
    - 预测跟踪与结果反馈
    - 用户画像管理
    - 知识图谱
    - LLM 驱动的记忆整合
    - 衰减遗忘与压缩
    - 会话生命周期管理
    - 智能上下文注入
    """

    def __init__(self, db_path=None, setting: dict = None):
        self.db_path = db_path or config.DB_PATH
        self._setting = setting
        self._embedding_index = None
        self._bm25_index = None
        self._prediction_tracker = None
        self._user_profile = None
        self._knowledge_graph = None
        self._session_new_memory_ids = []  # 本轮会话新增的记忆ID

    def _conn(self):
        return get_connection(self.db_path)

    @property
    def embedding_index(self) -> EmbeddingIndex:
        if self._embedding_index is None:
            self._embedding_index = EmbeddingIndex(self._setting)
        return self._embedding_index

    @property
    def prediction_tracker(self) -> PredictionTracker:
        if self._prediction_tracker is None:
            self._prediction_tracker = PredictionTracker(self.db_path)
        return self._prediction_tracker

    @property
    def user_profile(self) -> UserProfile:
        if self._user_profile is None:
            self._user_profile = UserProfile(self.db_path)
        return self._user_profile

    @property
    def knowledge_graph(self) -> KnowledgeGraph:
        if self._knowledge_graph is None:
            self._knowledge_graph = KnowledgeGraph(self.db_path)
        return self._knowledge_graph

    def init_tables(self):
        """创建记忆表（幂等操作）"""
        conn = self._conn()
        try:
            init_memory_tables(conn)
            # 加载 embedding 缓存
            if self.embedding_index.is_ready:
                count = self.embedding_index.load_cache(conn)
                if count > 0:
                    pass  # 静默加载
        finally:
            conn.close()

    # ── 长期记忆 CRUD ──────────────────────────────────────────────

    def save_memory(self, content: str, memory_type: str = "insight",
                    keywords: str = "", tags: str = "",
                    importance: float = 0.5,
                    source_session_id: str = None,
                    source_tool: str = None,
                    learned_what: str = "",
                    learned_why: str = "",
                    apply_when: str = "") -> str:
        """保存一条长期记忆，返回 JSON 结果"""
        memory_id = str(uuid.uuid4())
        half_life = DEFAULT_HALF_LIFE.get(memory_type, 30)

        if memory_type == "learning" and importance == 0.5:
            importance = 0.7

        kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        if not kw_list:
            kw_list = self._extract_keywords(content)

        now = datetime.now()

        # 生成 embedding
        embedding_bytes = None
        if self.embedding_index.is_ready:
            try:
                vec = self.embedding_index.encode_single(content)
                if vec:
                    embedding_bytes = EmbeddingIndex.vector_to_bytes(vec)
                    self.embedding_index.add_to_cache(memory_id, vec)
            except Exception:
                pass

        conn = self._conn()
        try:
            conn.execute("""
                INSERT INTO long_term_memories
                (id, memory_type, content, keywords_json, tags_json,
                 importance, access_count, last_accessed_at, decay_halflife_days,
                 source_session_id, source_tool, is_archived, embedding,
                 learned_what, learned_why, apply_when,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, FALSE, ?, ?, ?, ?, ?, ?)
            """, [memory_id, memory_type, content,
                  json.dumps(kw_list, ensure_ascii=False),
                  json.dumps(tag_list, ensure_ascii=False),
                  importance, now, half_life,
                  source_session_id, source_tool, embedding_bytes,
                  learned_what, learned_why, apply_when,
                  now, now])

            # 更新 BM25 索引标记
            self._bm25_index = None

            # 记录会话新增
            self._session_new_memory_ids.append(memory_id)

            # 自动提取知识图谱实体
            try:
                entities, relations = self.knowledge_graph.extract_from_memory(content, memory_type)
                for ent in entities[:5]:  # 限制每条记忆最多提取5个实体
                    self.knowledge_graph.add_entity(ent["type"], ent["name"])
            except Exception:
                pass

        finally:
            conn.close()

        return json.dumps({
            "status": "saved",
            "memory_id": memory_id,
            "memory_type": memory_type,
            "keywords": kw_list,
            "tags": tag_list,
            "has_embedding": embedding_bytes is not None,
        }, ensure_ascii=False)

    def search_memories(self, query: str, memory_type: str = "",
                        top_k: int = None) -> str:
        """搜索记忆，返回 JSON 结果列表。自动选择最佳搜索方式。"""
        if top_k is None:
            top_k = config.MEMORY_SEARCH_TOP_K

        query_keywords = self._extract_keywords(query)
        conn = self._conn()
        try:
            # 选择搜索策略
            if self.embedding_index.is_ready:
                results = self._search_with_embedding(query, query_keywords, memory_type, top_k, conn)
            else:
                results = self._search_with_bm25(query, query_keywords, memory_type, top_k, conn)

            # 更新访问计数
            access_ids = [m["id"] for m in results]
            if access_ids:
                now = datetime.now()
                for mid in access_ids:
                    conn.execute("""
                        UPDATE long_term_memories
                        SET access_count = access_count + 1, last_accessed_at = ?
                        WHERE id = ?
                    """, [now, mid])

            return json.dumps({
                "results": results,
                "total": len(results),
                "query_keywords": query_keywords,
                "search_method": "embedding" if self.embedding_index.is_ready else "bm25",
            }, ensure_ascii=False)
        finally:
            conn.close()

    def _search_with_embedding(self, query: str, query_keywords: list,
                               memory_type: str, top_k: int, conn) -> list:
        """使用 embedding 向量搜索 + 关键词精排"""
        query_vec = self.embedding_index.encode_single(query)
        if not query_vec:
            return self._search_with_bm25(query, query_keywords, memory_type, top_k, conn)

        # 向量粗排 top-50
        vector_results = self.embedding_index.search(query_vec, top_k=50)
        if not vector_results:
            return self._search_with_bm25(query, query_keywords, memory_type, top_k, conn)

        # 从数据库获取记忆详情
        mem_ids = [r[0] for r in vector_results]
        vector_scores = {r[0]: r[1] for r in vector_results}

        placeholders = ",".join(["?" for _ in mem_ids])
        conditions = [f"id IN ({placeholders})", "is_archived = FALSE"]
        params = list(mem_ids)
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)

        rows = conn.execute(f"""
            SELECT id, memory_type, content, keywords_json, tags_json,
                   importance, access_count, last_accessed_at,
                   decay_halflife_days, created_at,
                   COALESCE(learned_what, ''), COALESCE(learned_why, ''), COALESCE(apply_when, '')
            FROM long_term_memories
            WHERE {' AND '.join(conditions)}
        """, params).fetchall()

        # 综合评分：向量相似度 * 0.5 + 关键词匹配 * 0.2 + 衰减权重 * 0.3
        scored = []
        now = datetime.now()
        for row in rows:
            mem = {
                "id": row[0], "memory_type": row[1], "content": row[2],
                "keywords": json.loads(row[3]), "tags": json.loads(row[4]),
                "importance": row[5], "access_count": row[6],
                "last_accessed_at": str(row[7]) if row[7] else None,
                "decay_halflife_days": row[8],
                "created_at": str(row[9]) if row[9] else None,
                "learned_what": row[10], "learned_why": row[11], "apply_when": row[12],
            }

            # 向量相似度（已归一化到 0-1）
            vec_sim = vector_scores.get(row[0], 0)

            # 关键词匹配
            mem_keywords = set(json.loads(row[3]))
            query_kw_set = set(query_keywords)
            kw_overlap = len(mem_keywords & query_kw_set) / max(len(query_kw_set), 1) if query_kw_set else 0.3

            # 衰减权重
            last_acc = row[7] if row[7] else row[9]
            if last_acc:
                days = (now - last_acc).total_seconds() / 86400.0
            else:
                days = 999
            decay = math.exp(-0.693 * days / row[8]) if row[8] > 0 else 0

            effective = row[5] * decay  # importance * decay
            score = 0.5 * vec_sim + 0.2 * kw_overlap + 0.3 * effective

            if score > 0.05:
                mem["score"] = round(score, 4)
                mem["vector_similarity"] = round(vec_sim, 4)
                scored.append(mem)

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def _search_with_bm25(self, query: str, query_keywords: list,
                           memory_type: str, top_k: int, conn) -> list:
        """使用 BM25 搜索（无 embedding 时的回退）"""
        # 构建 BM25 索引
        if self._bm25_index is None:
            conditions = ["is_archived = FALSE"]
            params = []
            if memory_type:
                conditions.append("memory_type = ?")
                params.append(memory_type)

            rows = conn.execute(f"""
                SELECT id, content, keywords_json
                FROM long_term_memories
                WHERE {' AND '.join(conditions)}
            """, params).fetchall()

            docs = [(row[0], row[1], json.loads(row[2])) for row in rows]
            self._bm25_index = BM25Index()
            self._bm25_index.build(docs)

        # BM25 搜索
        bm25_results = self._bm25_index.search(query, query_keywords, top_k=top_k * 2)
        if not bm25_results:
            return self._search_keyword_fallback(query_keywords, memory_type, top_k, conn)

        # 获取记忆详情
        mem_ids = [r[0] for r in bm25_results]
        bm25_scores = {r[0]: r[1] for r in bm25_results}

        placeholders = ",".join(["?" for _ in mem_ids])
        conditions = [f"id IN ({placeholders})", "is_archived = FALSE"]
        params = list(mem_ids)
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)

        rows = conn.execute(f"""
            SELECT id, memory_type, content, keywords_json, tags_json,
                   importance, access_count, last_accessed_at,
                   decay_halflife_days, created_at,
                   COALESCE(learned_what, ''), COALESCE(learned_why, ''), COALESCE(apply_when, '')
            FROM long_term_memories
            WHERE {' AND '.join(conditions)}
        """, params).fetchall()

        now = datetime.now()
        scored = []
        for row in rows:
            mem = {
                "id": row[0], "memory_type": row[1], "content": row[2],
                "keywords": json.loads(row[3]), "tags": json.loads(row[4]),
                "importance": row[5], "access_count": row[6],
                "last_accessed_at": str(row[7]) if row[7] else None,
                "decay_halflife_days": row[8],
                "created_at": str(row[9]) if row[9] else None,
                "learned_what": row[10], "learned_why": row[11], "apply_when": row[12],
            }

            bm25_raw = bm25_scores.get(row[0], 0)
            # 归一化 BM25 分数
            max_bm25 = max(bm25_scores.values()) if bm25_scores else 1
            bm25_norm = bm25_raw / max_bm25 if max_bm25 > 0 else 0

            last_acc = row[7] if row[7] else row[9]
            days = (now - last_acc).total_seconds() / 86400.0 if last_acc else 999
            decay = math.exp(-0.693 * days / row[8]) if row[8] > 0 else 0
            effective = row[5] * decay

            score = 0.6 * bm25_norm + 0.4 * effective
            if score > 0.05:
                mem["score"] = round(score, 4)
                scored.append(mem)

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def _search_keyword_fallback(self, query_keywords: list,
                                  memory_type: str, top_k: int, conn) -> list:
        """最后的关键词 LIKE 回退"""
        conditions = ["is_archived = FALSE"]
        params = []
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)
        if query_keywords:
            kw_conds = []
            for kw in query_keywords[:5]:
                kw_conds.append("keywords_json LIKE ?")
                params.append(f"%{kw}%")
            if kw_conds:
                conditions.append(f"({' OR '.join(kw_conds)})")

        rows = conn.execute(f"""
            SELECT id, memory_type, content, keywords_json, tags_json,
                   importance, access_count, last_accessed_at,
                   decay_halflife_days, created_at,
                   COALESCE(learned_what, ''), COALESCE(learned_why, ''), COALESCE(apply_when, '')
            FROM long_term_memories
            WHERE {' AND '.join(conditions)}
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
        """, params + [top_k]).fetchall()

        results = []
        now = datetime.now()
        for row in rows:
            mem = {
                "id": row[0], "memory_type": row[1], "content": row[2],
                "keywords": json.loads(row[3]), "tags": json.loads(row[4]),
                "importance": row[5], "access_count": row[6],
                "last_accessed_at": str(row[7]) if row[7] else None,
                "decay_halflife_days": row[8],
                "created_at": str(row[9]) if row[9] else None,
                "learned_what": row[10], "learned_why": row[11], "apply_when": row[12],
            }
            last_acc = row[7] if row[7] else row[9]
            days = (now - last_acc).total_seconds() / 86400.0 if last_acc else 999
            decay = math.exp(-0.693 * days / row[8]) if row[8] > 0 else 0
            mem["score"] = round(row[5] * decay, 4)
            results.append(mem)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def update_memory(self, memory_id: str, content: str = "",
                      importance: float = -1, tags: str = "") -> str:
        """更新记忆字段"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id FROM long_term_memories WHERE id = ?", [memory_id]
            ).fetchone()
            if not row:
                return json.dumps({"error": f"记忆 {memory_id} 不存在"}, ensure_ascii=False)

            updates = ["updated_at = ?"]
            params = [datetime.now()]

            if content:
                updates.append("content = ?")
                params.append(content)
                new_kw = self._extract_keywords(content)
                updates.append("keywords_json = ?")
                params.append(json.dumps(new_kw, ensure_ascii=False))
                # 重新生成 embedding
                if self.embedding_index.is_ready:
                    try:
                        vec = self.embedding_index.encode_single(content)
                        if vec:
                            updates.append("embedding = ?")
                            params.append(EmbeddingIndex.vector_to_bytes(vec))
                            self.embedding_index.add_to_cache(memory_id, vec)
                    except Exception:
                        pass

            if importance >= 0:
                updates.append("importance = ?")
                params.append(importance)

            if tags:
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
                updates.append("tags_json = ?")
                params.append(json.dumps(tag_list, ensure_ascii=False))

            params.append(memory_id)
            conn.execute(
                f"UPDATE long_term_memories SET {', '.join(updates)} WHERE id = ?",
                params
            )
            self._bm25_index = None  # 标记需要重建
            return json.dumps({"status": "updated", "memory_id": memory_id}, ensure_ascii=False)
        finally:
            conn.close()

    def delete_memory(self, memory_id: str) -> str:
        """软删除（归档）记忆"""
        conn = self._conn()
        try:
            conn.execute("""
                UPDATE long_term_memories SET is_archived = TRUE, updated_at = ?
                WHERE id = ?
            """, [datetime.now(), memory_id])
            self.embedding_index.remove_from_cache(memory_id)
            self._bm25_index = None
            return json.dumps({"status": "archived", "memory_id": memory_id}, ensure_ascii=False)
        finally:
            conn.close()

    def list_memories(self, memory_type: str = "", limit: int = 20) -> str:
        """列出记忆"""
        conn = self._conn()
        try:
            conditions = ["is_archived = FALSE"]
            params = []
            if memory_type:
                conditions.append("memory_type = ?")
                params.append(memory_type)

            where_clause = " AND ".join(conditions)
            rows = conn.execute(f"""
                SELECT id, memory_type, content, keywords_json, tags_json,
                       importance, access_count, last_accessed_at, created_at
                FROM long_term_memories
                WHERE {where_clause}
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, params + [limit]).fetchall()

            results = []
            for row in rows:
                results.append({
                    "id": row[0], "memory_type": row[1],
                    "content": row[2][:100] + "..." if len(row[2]) > 100 else row[2],
                    "keywords": json.loads(row[3]), "tags": json.loads(row[4]),
                    "importance": row[5], "access_count": row[6],
                    "last_accessed_at": str(row[7]) if row[7] else None,
                    "created_at": str(row[8]) if row[8] else None,
                })

            return json.dumps({"results": results, "total": len(results)}, ensure_ascii=False)
        finally:
            conn.close()

    # ── 智能上下文注入 ────────────────────────────────────────────

    def get_context_block(self, query_text: str = "",
                          max_chars: int = None) -> str:
        """获取用于注入 system prompt 的记忆上下文块（多策略智能组装）

        分配:
        1. [30%] 当前查询最相关的记忆
        2. [25%] 用户教学/纠错记忆（learning 类型优先）
        3. [20%] 用户画像摘要
        4. [15%] 最近会话摘要
        5. [10%] 高置信度预测结果
        """
        if max_chars is None:
            max_chars = config.MEMORY_CONTEXT_MAX_CHARS

        sections = []

        # 1. 相关记忆 [30%]
        related_budget = int(max_chars * 0.3)
        if query_text:
            result = json.loads(self.search_memories(query_text, top_k=5))
            memories = result.get("results", [])
        else:
            memories = self._get_top_memories(5)
        related_text = self._format_memories_block(memories, related_budget)
        if related_text:
            sections.append(("相关记忆", related_text))

        # 2. 用户教学记忆 [25%]
        learning_budget = int(max_chars * 0.25)
        learning_text = self._get_learning_block(learning_budget)
        if learning_text:
            sections.append(("用户教学", learning_text))

        # 3. 用户画像 [20%]
        profile_budget = int(max_chars * 0.2)
        profile_text = self.user_profile.get_summary_text(profile_budget)
        if profile_text:
            sections.append(("用户画像", profile_text))

        # 4. 最近会话摘要 [15%]
        session_budget = int(max_chars * 0.15)
        session_text = self._get_session_summary_block(session_budget)
        if session_text:
            sections.append(("最近会话", session_text))

        # 5. 预测结果 [10%]
        pred_budget = int(max_chars * 0.1)
        pred_text = self._get_prediction_block(pred_budget)
        if pred_text:
            sections.append(("预测记录", pred_text))

        if not sections:
            return ""

        lines = []
        for label, text in sections:
            lines.append(f"### {label}")
            lines.append(text)

        return "\n".join(lines)

    def _get_top_memories(self, limit: int) -> list:
        """获取最重要+最近的记忆"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT id, memory_type, content, keywords_json, tags_json,
                       importance, access_count, last_accessed_at,
                       decay_halflife_days, created_at,
                       COALESCE(learned_what, ''), COALESCE(learned_why, ''), COALESCE(apply_when, '')
                FROM long_term_memories
                WHERE is_archived = FALSE
                ORDER BY importance DESC, last_accessed_at DESC NULLS LAST
                LIMIT ?
            """, [limit]).fetchall()

            now = datetime.now()
            memories = []
            for row in rows:
                mem = {
                    "id": row[0], "memory_type": row[1], "content": row[2],
                    "keywords": json.loads(row[3]), "tags": json.loads(row[4]),
                    "importance": row[5], "decay_halflife_days": row[8],
                    "learned_what": row[10], "learned_why": row[11], "apply_when": row[12],
                }
                last_acc = row[7] if row[7] else row[9]
                days = (now - last_acc).total_seconds() / 86400.0 if last_acc else 999
                eff = row[5] * math.exp(-0.693 * days / row[8]) if row[8] > 0 else 0
                if eff >= config.MEMORY_DECAY_THRESHOLD:
                    mem["score"] = round(eff, 4)
                    memories.append(mem)
            return memories
        finally:
            conn.close()

    def _get_learning_block(self, max_chars: int) -> str:
        """获取用户教学记忆块"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT content, learned_what, learned_why, apply_when, keywords_json
                FROM long_term_memories
                WHERE memory_type = 'learning' AND is_archived = FALSE
                ORDER BY importance DESC
                LIMIT 5
            """).fetchall()

            if not rows:
                return ""

            lines = []
            total = 0
            for row in rows:
                line = f"- {row[0][:80]}"
                if row[1]:
                    line += f"\n  学到: {row[1]}"
                if row[3]:
                    line += f"\n  应用时机: {row[3]}"
                if total + len(line) > max_chars:
                    break
                lines.append(line)
                total += len(line) + 1

            return "\n".join(lines)
        finally:
            conn.close()

    def _get_session_summary_block(self, max_chars: int) -> str:
        """获取最近会话摘要"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT summary, stocks_json, started_at
                FROM memory_sessions
                WHERE summary IS NOT NULL AND summary != ''
                ORDER BY started_at DESC
                LIMIT 2
            """).fetchall()

            if not rows:
                return ""

            lines = []
            total = 0
            for row in rows:
                stocks = json.loads(row[1]) if row[1] else []
                stocks_str = ",".join(stocks[:3]) if stocks else ""
                prefix = f"[{stocks_str}] " if stocks_str else ""
                line = f"- {prefix}{row[0][:100]}"
                if total + len(line) > max_chars:
                    break
                lines.append(line)
                total += len(line) + 1

            return "\n".join(lines)
        finally:
            conn.close()

    def _get_prediction_block(self, max_chars: int) -> str:
        """获取最近预测结果"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT stock_code, direction, outcome, actual_return_pct, created_at
                FROM predictions
                WHERE outcome != 'pending'
                ORDER BY checked_at DESC
                LIMIT 3
            """).fetchall()

            if not rows:
                return ""

            lines = []
            total = 0
            for row in rows:
                outcome_label = {"correct": "正确", "wrong": "错误", "partial": "部分"}.get(row[2], row[2])
                ret_str = f"{row[3]:+.1f}%" if row[3] is not None else ""
                line = f"- {row[0]} {row[1]} → {outcome_label} {ret_str}"
                if total + len(line) > max_chars:
                    break
                lines.append(line)
                total += len(line) + 1

            return "\n".join(lines)
        finally:
            conn.close()

    def _format_memories_block(self, memories: list, max_chars: int) -> str:
        """格式化记忆列表为紧凑文本"""
        if not memories:
            return ""

        type_labels = {
            "preference": "偏好", "insight": "分析结论",
            "fact": "事实", "context": "上下文", "summary": "摘要",
            "learning": "用户教学",
        }
        lines = []
        total = 0
        for mem in memories:
            label = type_labels.get(mem["memory_type"], mem["memory_type"])
            keywords_str = ", ".join(mem.get("keywords", [])[:3])
            line = f"[{label}] {mem['content'][:120]}"
            if keywords_str:
                line += f" (关键词: {keywords_str})"
            if mem["memory_type"] == "learning":
                learned = mem.get("learned_what", "")
                when = mem.get("apply_when", "")
                if learned:
                    line += f"\n  学到: {learned}"
                if when:
                    line += f"\n  应用时机: {when}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line) + 1

        return "\n".join(lines)

    # ── LLM 驱动的记忆整合 ───────────────────────────────────────

    def compress_memories_with_llm(self, llm_client=None) -> str:
        """使用 LLM 整合本轮新增的同类型记忆

        Args:
            llm_client: LLMClient 实例，用于调用 LLM
        """
        if not self._session_new_memory_ids:
            return json.dumps({"compressed": 0, "message": "本轮无新增记忆"}, ensure_ascii=False)

        conn = self._conn()
        try:
            # 获取本轮新增的记忆
            placeholders = ",".join(["?" for _ in self._session_new_memory_ids])
            rows = conn.execute(f"""
                SELECT id, memory_type, content, keywords_json, tags_json, importance
                FROM long_term_memories
                WHERE id IN ({placeholders}) AND is_archived = FALSE
                ORDER BY memory_type, created_at
            """, self._session_new_memory_ids).fetchall()

            if len(rows) < 3:
                return json.dumps({"compressed": 0, "message": "新增记忆不足3条，跳过整合"},
                                  ensure_ascii=False)

            # 按类型分组
            groups = {}
            for row in rows:
                mt = row[1]
                if mt not in groups:
                    groups[mt] = []
                groups[mt].append({
                    "id": row[0], "content": row[2],
                    "keywords": json.loads(row[3]),
                    "tags": json.loads(row[4]),
                    "importance": row[5],
                })

            compressed = 0
            for mt, members in groups.items():
                if len(members) < 3:
                    continue

                # 每次最多合并5条
                batch = members[:5]
                if llm_client:
                    merged_content = self._llm_consolidate(llm_client, mt, batch)
                else:
                    merged_content = self._rule_consolidate(mt, batch)

                if merged_content:
                    # 合并关键词和标签
                    all_kw = set()
                    all_tags = set()
                    max_imp = 0
                    for m in batch:
                        all_kw.update(m["keywords"])
                        all_tags.update(m["tags"])
                        max_imp = max(max_imp, m["importance"])

                    # 保留第一条，归档其余
                    keep_id = batch[0]["id"]
                    archive_ids = [m["id"] for m in batch[1:]]

                    now = datetime.now()
                    conn.execute("""
                        UPDATE long_term_memories
                        SET content = ?, keywords_json = ?, tags_json = ?,
                            importance = ?, updated_at = ?
                        WHERE id = ?
                    """, [merged_content,
                          json.dumps(list(all_kw), ensure_ascii=False),
                          json.dumps(list(all_tags), ensure_ascii=False),
                          min(1.0, max_imp + 0.05), now, keep_id])

                    for aid in archive_ids:
                        conn.execute("""
                            UPDATE long_term_memories SET is_archived = TRUE, updated_at = ?
                            WHERE id = ?
                        """, [now, aid])
                        self.embedding_index.remove_from_cache(aid)

                    # 重新生成 embedding
                    if self.embedding_index.is_ready:
                        try:
                            vec = self.embedding_index.encode_single(merged_content)
                            if vec:
                                conn.execute("""
                                    UPDATE long_term_memories SET embedding = ? WHERE id = ?
                                """, [EmbeddingIndex.vector_to_bytes(vec), keep_id])
                                self.embedding_index.add_to_cache(keep_id, vec)
                        except Exception:
                            pass

                    compressed += len(archive_ids)

            self._bm25_index = None
            return json.dumps({"compressed": compressed}, ensure_ascii=False)
        finally:
            conn.close()

    def _llm_consolidate(self, llm_client, memory_type: str,
                         memories: list) -> str | None:
        """调用 LLM 合并记忆"""
        type_label = {
            "insight": "分析结论", "learning": "用户教学",
            "fact": "市场事实", "preference": "用户偏好",
        }.get(memory_type, memory_type)

        contents = "\n".join([f"{i+1}. {m['content']}" for i, m in enumerate(memories)])
        prompt = f"""请将以下 {len(memories)} 条{type_label}合并为一条精炼总结。

要求：
- 保留所有关键数据点（股票代码、价位、指标数值）
- 去除重复信息
- 保留最重要的判断和结论
- 如有矛盾，保留更新的信息
- 输出一条简洁的合并结果，不超过200字

原始记忆：
{contents}

合并结果："""

        try:
            response = llm_client.chat([{"role": "user", "content": prompt}])
            result = response.get("content", "").strip()
            if result and len(result) > 10:
                return result[:500]
        except Exception:
            pass
        return None

    def _rule_consolidate(self, memory_type: str, memories: list) -> str | None:
        """规则合并（不依赖 LLM）"""
        contents = [m["content"] for m in memories]
        # 简单拼接 + 截断
        merged = f"[合并{len(contents)}条{memory_type}] " + "; ".join(contents)
        return merged[:500] if len(merged) > 500 else merged

    # ── 衰减遗忘 ───────────────────────────────────────────────────

    def decay_and_forget(self, threshold: float = None) -> str:
        """归档有效权重低于阈值的记忆"""
        if threshold is None:
            threshold = config.MEMORY_DECAY_THRESHOLD

        conn = self._conn()
        try:
            now = datetime.now()
            rows = conn.execute("""
                SELECT id, importance, access_count, last_accessed_at,
                       decay_halflife_days, created_at
                FROM long_term_memories
                WHERE is_archived = FALSE
            """).fetchall()

            archived = []
            for row in rows:
                mid, importance, acc_count, last_acc, half_life, created = row
                last_time = last_acc or created
                if last_time:
                    days = (now - last_time).total_seconds() / 86400.0
                else:
                    days = 999
                eff = importance * math.exp(-0.693 * days / half_life) if half_life > 0 else 0
                if eff < threshold:
                    conn.execute("""
                        UPDATE long_term_memories
                        SET is_archived = TRUE, updated_at = ?
                        WHERE id = ?
                    """, [now, mid])
                    archived.append(mid)
                    self.embedding_index.remove_from_cache(mid)

            if archived:
                self._bm25_index = None

            return json.dumps({
                "archived_count": len(archived),
                "total_checked": len(rows),
                "threshold": threshold,
            }, ensure_ascii=False)
        finally:
            conn.close()

    # ── 会话生命周期 ────────────────────────────────────────────────

    def start_session(self, session_id: str) -> str:
        """记录会话开始"""
        self._session_new_memory_ids = []
        conn = self._conn()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO memory_sessions (session_id, started_at)
                VALUES (?, ?)
            """, [session_id, datetime.now()])
            return session_id
        finally:
            conn.close()

    def end_session(self, session_id: str, summary: str = "",
                    topics: list = None, stocks: list = None,
                    message_count: int = 0) -> str:
        """结束会话，保存摘要"""
        conn = self._conn()
        try:
            now = datetime.now()
            conn.execute("""
                UPDATE memory_sessions
                SET ended_at = ?, summary = ?, topics_json = ?,
                    stocks_json = ?, message_count = ?
                WHERE session_id = ?
            """, [now, summary,
                  json.dumps(topics or [], ensure_ascii=False),
                  json.dumps(stocks or [], ensure_ascii=False),
                  message_count, session_id])

            if summary:
                self.save_memory(
                    content=summary,
                    memory_type="summary",
                    keywords=",".join(stocks or []),
                    tags="会话摘要",
                    importance=0.4,
                    source_session_id=session_id,
                )

            return json.dumps({"status": "session_ended", "session_id": session_id}, ensure_ascii=False)
        finally:
            conn.close()

    def get_recent_session_summary(self, limit: int = 3) -> str:
        """获取最近会话摘要"""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT session_id, started_at, ended_at, summary,
                       topics_json, stocks_json, message_count
                FROM memory_sessions
                WHERE summary IS NOT NULL AND summary != ''
                ORDER BY started_at DESC
                LIMIT ?
            """, [limit]).fetchall()

            results = []
            for row in rows:
                results.append({
                    "session_id": row[0],
                    "started_at": str(row[1]) if row[1] else None,
                    "ended_at": str(row[2]) if row[2] else None,
                    "summary": row[3],
                    "topics": json.loads(row[4]) if row[4] else [],
                    "stocks": json.loads(row[5]) if row[5] else [],
                    "message_count": row[6],
                })

            return json.dumps({"sessions": results}, ensure_ascii=False)
        finally:
            conn.close()

    # ── 学习信号检测 ───────────────────────────────────────────────

    def detect_learning_signals(self, user_message: str,
                                 agent_response: str = "") -> list[dict]:
        """检测用户消息中的教学/纠错信号"""
        signals = []

        # 纠错信号
        correction_patterns = [
            (r'(?:你)?分析错了', "纠错"),
            (r'不对[，,！!。.]', "纠错"),
            (r'应该[是看]', "纠错"),
            (r'你忽略了', "纠错"),
            (r'这个判断有问题', "纠错"),
            (r'你的逻辑不对', "纠错"),
            (r'不能只看', "纠错"),
            (r'而不是', "纠错"),
        ]
        for pattern, signal_type in correction_patterns:
            if re.search(pattern, user_message):
                signals.append({"type": signal_type, "source": "user_message"})
                break

        # 教学信号
        teaching_patterns = [
            (r'记住[：:]', "教学"),
            (r'以后要注意', "教学"),
            (r'你要知道', "教学"),
            (r'我教你', "教学"),
            (r'技巧[：:]', "教学"),
            (r'经验[：:]', "教学"),
        ]
        for pattern, signal_type in teaching_patterns:
            if re.search(pattern, user_message):
                signals.append({"type": signal_type, "source": "user_message"})
                break

        # Agent 自认错误
        if agent_response:
            self_correction = [
                r'我[的之]前.{0,10}(?:判断|分析|结论).{0,10}(?:有误|错误|不对|不准确)',
                r'你说得对',
                r'感谢?指[正出]',
                r'确实.{0,5}(?:忽略|遗漏|错了)',
            ]
            for pattern in self_correction:
                if re.search(pattern, agent_response):
                    signals.append({"type": "self_correction", "source": "agent_response"})
                    break

        return signals

    # ── 内部方法 ────────────────────────────────────────────────────

    _TECH_TERMS = {
        "金叉", "死叉", "超买", "超卖", "背离", "突破", "支撑", "压力",
        "放量", "缩量", "涨停", "跌停", "反弹", "回调", "均线", "多头",
        "空头", "整理", "震荡", "趋势", "短线", "中线", "长线", "量价",
        "布林带", "布林", "MACD", "RSI", "KDJ", "MA5", "MA10", "MA20",
        "MACD背离", "RSI背离", "MACD金叉", "MACD死叉", "KDJ金叉", "KDJ死叉",
        "成交量", "换手率", "涨跌幅", "主力", "散户", "龙头", "跟风",
        "首板", "连板", "打板", "低吸", "半路", "封板", "炸板",
        "加仓", "减仓", "止损", "止盈", "仓位", "利好", "利空",
        "高开", "低开", "跳空", "缺口", "分时", "集合竞价",
        "超跌", "底部", "顶部", "平台", "箱体", "三角形", "旗形",
    }

    def _extract_keywords(self, text: str) -> list:
        """从文本中提取关键词"""
        keywords = set()
        codes = re.findall(r'\b[036]\d{5}\b', text)
        keywords.update(codes)
        for term in self._TECH_TERMS:
            if term in text:
                keywords.add(term)
        segments = re.split(r'[,，。；：！？\s\n\r\t（）()[\]【】""]+', text)
        for seg in segments:
            seg = seg.strip()
            if re.match(r'^[一-鿿]{2,4}$', seg):
                keywords.add(seg)
        return list(keywords)[:15]
