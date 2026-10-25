"""用户交易画像管理"""

import json
import re
from datetime import datetime

from deeppulse import config
from deeppulse.src.database import get_connection

# 多值画像键：新值与旧值取并集（偏好指标/关注板块等不因后写覆盖而丢失历史）
MULTI_VALUE_KEYS = {"preferred_indicators", "watched_sectors", "watched_stocks"}
MULTI_VALUE_MAX = 8

# 否定词表：命中词前的近距窗口内出现即视为否定表达，不产生画像信号
_NEGATION_TOKENS = ("不", "别", "没", "勿", "讨厌", "拒绝", "远离")

# 画像键白名单（工具与提取层共用，防任意字符串入库）
VALID_PROFILE_KEYS = {
    "trading_style",
    "risk_tolerance",
    "preferred_indicators",
    "watched_sectors",
    "watched_stocks",
    "position_sizing",
    "profit_target",
    "stop_loss_habit",
    "trading_frequency",
}


def _clamp_confidence(confidence) -> float:
    """置信度夹取 [0,1]（越界不是合法输入语义，按边界处理并由上层回告）"""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, c))


def _split_multi(value: str) -> list[str]:
    """多值字段按分隔符拆分（、，,;；/ 空白）"""
    parts = re.split(r"[、，,;；/|\s]+", (value or "").strip())
    return [p for p in parts if p]


def _merge_multi(old_value: str, new_value: str) -> str:
    """多值键并集合并，保序去重，截断上限"""
    merged = list(dict.fromkeys(_split_multi(old_value) + _split_multi(new_value)))
    return "、".join(merged[:MULTI_VALUE_MAX])


def _is_negated(text: str, match_start: int) -> bool:
    """命中词近距（≤4 字、同小句）内是否有否定词

    「我不喜欢打板」→ 打板 被否定；「别打板，我做低吸」→ 只否定打板，
    标点后的小句另算（否定不跨逗号传播）；「别推荐激进」→ 激进 仍被否定
    （否定词与命中词在同一小句内即可）。
    """
    window = text[max(0, match_start - 4) : match_start]
    # 只看命中词所在小句（标点之后的部分）
    clause = re.split(r"[，。；、,.!?！？:：;；\s]", window)[-1]
    return any(t in clause for t in _NEGATION_TOKENS)


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
        """更新用户画像

        冲突时保留置信度更高一侧的值：低置信新值不得推翻高置信旧共识；
        多值键（偏好指标/关注板块/关注股票）走并集合并而非覆盖。
        """
        if key not in VALID_PROFILE_KEYS:
            return json.dumps(
                {"error": f"未知画像键: {key!r}，允许值: {sorted(VALID_PROFILE_KEYS)}"}, ensure_ascii=False
            )
        if not (value or "").strip():
            return json.dumps({"error": "value 不能为空"}, ensure_ascii=False)
        value = value.strip()

        raw_conf = confidence
        confidence = _clamp_confidence(confidence)
        conf_clamped = False
        try:
            conf_clamped = float(raw_conf) != confidence
        except (TypeError, ValueError):
            conf_clamped = True

        conn = self._conn()
        try:
            now = datetime.now()
            # 检查是否已存在
            existing = conn.execute(
                "SELECT value, confidence, source_memory_ids FROM user_profile WHERE key = ?", [key]
            ).fetchone()

            if existing:
                old_value, old_conf, old_ids_raw = existing
                old_conf = _clamp_confidence(old_conf if old_conf is not None else 0.5)

                if key in MULTI_VALUE_KEYS:
                    # 多值键：并集合并，置信度同值加成语义（合并视为一致证据）
                    final_value = _merge_multi(old_value or "", value)
                    new_conf = min(1.0, (old_conf + confidence) / 2 + 0.05)
                elif (old_value or "").strip() == value:
                    new_conf = min(1.0, (old_conf + confidence) / 2 + 0.05)
                    final_value = value
                else:
                    # 冲突：保留置信度更高一侧的值（低置信口误不得翻转已确立画像）
                    final_value = value if confidence > old_conf else (old_value or value)
                    new_conf = max(0.1, (old_conf + confidence) / 2 - 0.1)

                # source_memory_ids 合并而非覆盖，容错空/NULL/坏 JSON
                try:
                    old_ids = json.loads(old_ids_raw) if old_ids_raw else []
                    if not isinstance(old_ids, list):
                        old_ids = []
                except (json.JSONDecodeError, TypeError, ValueError):
                    old_ids = []
                merged_ids = list(dict.fromkeys(old_ids + list(source_memory_ids or [])))
                conn.execute(
                    """
                    UPDATE user_profile
                    SET value = ?, confidence = ?, source_memory_ids = ?, updated_at = ?
                    WHERE key = ?
                """,
                    [final_value, new_conf, json.dumps(merged_ids, ensure_ascii=False), now, key],
                )
            else:
                new_conf = confidence
                final_value = value
                conn.execute(
                    """
                    INSERT INTO user_profile (key, value, confidence, source_memory_ids, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    [key, value, new_conf, json.dumps(source_memory_ids or [], ensure_ascii=False), now],
                )

            result = {"status": "updated", "key": key, "value": final_value, "confidence": new_conf}
            if conf_clamped:
                result["confidence_clamped"] = True
            return json.dumps(result, ensure_ascii=False)
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
        """从用户消息中提取画像信号（规则层）

        - 否定窗口：「我不喜欢打板」「别推荐追高」不产生信号；
        - 消息级时序：逐条扫描、后消息覆盖前消息的同键信号（「以前低吸，现在改打板」→ 打板型）。
        """
        signals_by_key: dict[str, dict] = {}

        for msg in user_messages:
            msg = msg or ""
            for sig in self._extract_from_message(msg):
                # 后发言优先：同键直接覆盖（比全量拼接后 elif 链的"先命中先赢"诚实）
                signals_by_key[sig["key"]] = sig

        return list(signals_by_key.values())

    def _extract_from_message(self, msg: str) -> list[dict]:
        signals = []

        # 交易风格：句内所有候选按位置取最后一个未被否定的（「以前低吸，现在改打板」→ 打板型）
        style_patterns = [
            (r"低吸|抄底|回调买入", "低吸型"),
            (r"打板|追涨停|追高", "打板型"),
            (r"半路|点火|启动", "半路型"),
            (r"接力|二板|三板", "接力型"),
        ]
        style_hits = []
        for pattern, style in style_patterns:
            for m in re.finditer(pattern, msg):
                if not _is_negated(msg, m.start()):
                    style_hits.append((m.start(), style))
        if style_hits:
            style_hits.sort()
            signals.append({"key": "trading_style", "value": style_hits[-1][1], "confidence": 0.6})

        # 风险偏好（词表与动态提示词适配分支对齐：保守/稳健 同义）；同样最后命中优先
        risk_patterns = [
            (r"保守|稳健|风险低", "保守型"),
            (r"激进|高风险|满仓", "激进型"),
        ]
        risk_hits = []
        for pattern, risk in risk_patterns:
            for m in re.finditer(pattern, msg):
                if not _is_negated(msg, m.start()):
                    risk_hits.append((m.start(), risk))
        if risk_hits:
            risk_hits.sort()
            signals.append({"key": "risk_tolerance", "value": risk_hits[-1][1], "confidence": 0.5})

        # 止损习惯
        stop_match = re.search(r"止损[设为用]*(\d+(?:\.\d+)?)\s*%", msg)
        if stop_match and not _is_negated(msg, stop_match.start()):
            signals.append({"key": "stop_loss_habit", "value": f"{stop_match.group(1)}%", "confidence": 0.7})

        # 偏好指标
        indicators = []
        for ind in ["MACD", "RSI", "KDJ", "布林", "均线", "量价"]:
            if ind in msg:
                indicators.append(ind)
        if indicators:
            signals.append({"key": "preferred_indicators", "value": "、".join(indicators), "confidence": 0.4})

        # 关注板块
        for m in re.finditer(r"(?:关注|做|看好|研究)\s*([一-鿿]{2,6}(?:板块|行业|概念|股))", msg):
            if not _is_negated(msg, m.start()):
                signals.append({"key": "watched_sectors", "value": m.group(1), "confidence": 0.5})
                break

        return signals
