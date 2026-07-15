"""结构化交易知识图谱"""

import json
import re
import uuid
from datetime import datetime

from deeppulse import config
from deeppulse.src.database import get_connection


class KnowledgeGraph:
    """结构化交易知识图谱"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    def _conn(self):
        return get_connection(self.db_path)

    def add_entity(self, entity_type: str, name: str, attributes: dict = None) -> str:
        """添加或更新知识实体"""
        entity_id = str(uuid.uuid4())
        now = datetime.now()
        conn = self._conn()
        try:
            # 检查是否已存在同名同类型实体
            existing = conn.execute(
                """
                SELECT id FROM knowledge_entities
                WHERE entity_type = ? AND name = ?
            """,
                [entity_type, name],
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE knowledge_entities
                    SET attributes_json = ?, updated_at = ?
                    WHERE id = ?
                """,
                    [json.dumps(attributes or {}, ensure_ascii=False), now, existing[0]],
                )
                return existing[0]

            conn.execute(
                """
                INSERT INTO knowledge_entities (id, entity_type, name, attributes_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                [entity_id, entity_type, name, json.dumps(attributes or {}, ensure_ascii=False), now, now],
            )
            return entity_id
        finally:
            conn.close()

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        weight: float = 1.0,
        evidence_memory_ids: list[str] = None,
    ) -> str:
        """添加知识关系"""
        rel_id = str(uuid.uuid4())
        conn = self._conn()
        try:
            # 检查是否已存在相同关系
            existing = conn.execute(
                """
                SELECT id, weight FROM knowledge_relations
                WHERE source_id = ? AND target_id = ? AND relation_type = ?
            """,
                [source_id, target_id, relation_type],
            ).fetchone()

            if existing:
                # 更新权重（取平均）
                new_weight = (existing[1] + weight) / 2
                conn.execute(
                    """
                    UPDATE knowledge_relations SET weight = ? WHERE id = ?
                """,
                    [new_weight, existing[0]],
                )
                return existing[0]

            conn.execute(
                """
                INSERT INTO knowledge_relations
                (id, source_id, target_id, relation_type, weight, evidence_memory_ids, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    rel_id,
                    source_id,
                    target_id,
                    relation_type,
                    weight,
                    json.dumps(evidence_memory_ids or [], ensure_ascii=False),
                    datetime.now(),
                ],
            )
            return rel_id
        finally:
            conn.close()

    def query_related(self, entity_name: str = None, entity_type: str = None, relation_type: str = None) -> str:
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
                results.append(
                    {
                        "source": {"name": row[0], "type": row[1]},
                        "relation": row[2],
                        "target": {"name": row[3], "type": row[4]},
                        "weight": row[5],
                    }
                )

            return json.dumps({"results": results, "total": len(results)}, ensure_ascii=False)
        finally:
            conn.close()

    def extract_from_memory(self, content: str, memory_type: str = "") -> list[dict]:
        """从记忆内容中提取实体和关系"""
        entities = []
        relations = []

        # 提取技术指标实体
        indicator_patterns = {
            r"RSI[<>]=?\s*\d+": "indicator",
            r"MACD\s*金叉": "indicator",
            r"MACD\s*死叉": "indicator",
            r"KDJ\s*金叉": "indicator",
            r"KDJ\s*死叉": "indicator",
            r"MA\d+[<>]MA\d+": "indicator",
            r"放量": "indicator",
            r"缩量": "indicator",
        }
        for pattern, etype in indicator_patterns.items():
            matches = re.findall(pattern, content)
            for m in matches:
                entities.append({"type": etype, "name": m.strip()})

        # 提取股票代码实体
        codes = re.findall(r"\b([036]\d{5})\b", content)
        for code in codes:
            entities.append({"type": "stock", "name": code})

        # 提取战法实体
        strategy_names = re.findall(r"([一-鿿]{2,8}战法)", content)
        for sn in strategy_names:
            entities.append({"type": "strategy", "name": sn})

        # 提取规则关系
        if memory_type == "learning":
            # 学习记忆中的 "X 应用于 Y" 关系
            apply_matches = re.findall(r"([一-鿿\w]+)\s*(?:适用于|应用于|触发)", content)
            for m in apply_matches:
                relations.append({"source": m, "target": "user_rule", "type": "supports"})

        return entities, relations
