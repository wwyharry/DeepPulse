"""用户交易画像管理"""

import json
import re
from datetime import datetime

import config
from src.database import get_connection


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

            return json.dumps({"profile": profile, "total": len(profile)}, ensure_ascii=False)
        finally:
            conn.close()

    def update_profile(self, key: str, value: str, confidence: float = 0.5, source_memory_ids: list[str] = None) -> str:
        """更新用户画像"""
        conn = self._conn()
        try:
            now = datetime.now()
            # 检查是否已存在
            existing = conn.execute("SELECT confidence FROM user_profile WHERE key = ?", [key]).fetchone()

            if existing:
                # 合并置信度：取加权平均
                old_conf = existing[0]
                new_conf = min(1.0, (old_conf + confidence) / 2 + 0.05)
                conn.execute(
                    """
                    UPDATE user_profile
                    SET value = ?, confidence = ?, source_memory_ids = ?, updated_at = ?
                    WHERE key = ?
                """,
                    [value, new_conf, json.dumps(source_memory_ids or [], ensure_ascii=False), now, key],
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_profile (key, value, confidence, source_memory_ids, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    [key, value, confidence, json.dumps(source_memory_ids or [], ensure_ascii=False), now],
                )

            return json.dumps(
                {"status": "updated", "key": key, "value": value, "confidence": confidence}, ensure_ascii=False
            )
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
        if re.search(r"低吸|抄底|回调买入", combined):
            signals.append({"key": "trading_style", "value": "低吸型", "confidence": 0.6})
        elif re.search(r"打板|追涨停|追高", combined):
            signals.append({"key": "trading_style", "value": "打板型", "confidence": 0.6})
        elif re.search(r"半路|点火|启动", combined):
            signals.append({"key": "trading_style", "value": "半路型", "confidence": 0.6})
        elif re.search(r"接力|二板|三板", combined):
            signals.append({"key": "trading_style", "value": "接力型", "confidence": 0.6})

        # 风险偏好
        if re.search(r"保守|稳健|风险低", combined):
            signals.append({"key": "risk_tolerance", "value": "保守型", "confidence": 0.5})
        elif re.search(r"激进|高风险|满仓", combined):
            signals.append({"key": "risk_tolerance", "value": "激进型", "confidence": 0.5})

        # 止损习惯
        stop_match = re.search(r"止损[设为用]*(\d+(?:\.\d+)?)\s*%", combined)
        if stop_match:
            signals.append({"key": "stop_loss_habit", "value": f"{stop_match.group(1)}%", "confidence": 0.7})

        # 偏好指标
        indicators = []
        for ind in ["MACD", "RSI", "KDJ", "布林", "均线", "量价"]:
            if ind in combined:
                indicators.append(ind)
        if indicators:
            signals.append({"key": "preferred_indicators", "value": "、".join(indicators), "confidence": 0.4})

        # 关注板块
        sector_match = re.findall(r"(?:关注|做|看好|研究)\s*([一-鿿]{2,6}(?:板块|行业|概念|股))", combined)
        if sector_match:
            signals.append({"key": "watched_sectors", "value": "、".join(sector_match[:3]), "confidence": 0.5})

        return signals
