"""会话持久化 JSON store —— cap 200、原子写、坏文件容错、回灌 [新 system]+stored[1:]"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

SESSION_CAP = 200
AUTO_TITLE_LEN = 20


def is_valid_session_id(session_id: str) -> bool:
    """session_id 白名单校验（与 SessionStore._path 同口径）：仅 [A-Za-z0-9_-]。

    API 入口统一先过这道闸——否则 chat 收下 "bad/id"/"../evil" 正常出帧到 done，
    落盘却被 _path 拒掉零文件，同 id 在 GET/PATCH/DELETE 却 404，语义撕裂。
    """
    s = session_id or ""
    return bool(s) and all(c.isalnum() or c in "-_" for c in s)


def _now() -> str:
    """ISO 时间戳（含微秒，同格式可字典序排序）"""
    return datetime.now().isoformat()


def auto_title(message: str) -> str:
    """自动标题：第一条用户消息前 20 字符"""
    return (message or "").strip()[:AUTO_TITLE_LEN]


def is_auto_title(stored: dict) -> bool:
    """判断标题是否仍是自动生成（未被手动改名）。

    依据：标题为空；或等于首条/末条用户消息前 20 字（自动标题两种形态）。
    候选与 auto_title 同口径：先 strip 再截 20（否则消息带前后空白时恒 False，
    自动标题刷新永久失效；反向还会把恰等于某 um[:20] 的手工标题误判为自动）。
    零消息 + 非空标题：标题来自 create/rename 手工命名，不是自动——
    否则首轮长分析中 PATCH 改名会被收尾 _persist 的标题快照冲掉（m1）。
    """
    title = (stored.get("title") or "").strip()
    if not title:
        return True
    um = stored.get("user_messages") or []
    if not um:
        return False
    candidates = {auto_title(str(um[0])), auto_title(str(um[-1]))}
    return title in candidates


def restore_agent_state(agent, stored: dict) -> None:
    """把存储的消息回灌进 agent：`[新 system] + stored[1:]`，附带用户/股票/主题列表。

    - messages[0] 用 agent 新建的 system（旧 system 时变陈旧不可回灌）；
    - `tool_calls[].function.arguments` 是 JSON 字符串，原样保留不再解码；
    - protocol 不一致时仅跳过消息体回灌（openai/anthropic 形状互不兼容）；
      侧表（user_messages/stocks/topics）与协议无关，必须照常回灌——
      否则切 protocol 重建 agent 后侧表全空，done 后 _persist 整表覆盖会永久抹掉历史侧表。
    """
    if agent is None or not stored:
        return
    stored_proto = stored.get("protocol")
    agent_proto = None
    client = getattr(agent, "client", None)
    if client is not None:
        agent_proto = getattr(client, "protocol", None)
    protocol_mismatch = bool(
        stored_proto and agent_proto and str(stored_proto) != str(agent_proto)
    )

    msgs = stored.get("messages") or []
    # 侧表仅在有历史可回灌时覆盖（全新空记录不冲掉构造期初值）；与协议无关，先于协议闸门执行
    if msgs or (stored.get("user_messages") or []):
        if hasattr(agent, "_user_messages"):
            agent._user_messages = list(stored.get("user_messages") or [])
        for attr in ("_session_stocks", "_session_topics"):
            if not hasattr(agent, attr):
                continue
            values = list(stored.get(attr[1:]) or [])  # 存储键不带下划线前缀
            cur = getattr(agent, attr)
            setattr(agent, attr, set(values) if isinstance(cur, set) else list(values))

    # 协议不一致只跳过消息体（形状互不兼容），侧表已回灌
    if protocol_mismatch:
        return
    if msgs and getattr(agent, "messages", None):
        agent.messages = [agent.messages[0]] + list(msgs[1:])


class SessionStore:
    """会话 JSON 文件存储（{root}/{session_id}.json）"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        # 防路径穿越：session_id 只允许文件名安全字符（与 is_valid_session_id 同口径）
        if not is_valid_session_id(session_id):
            raise ValueError(f"非法 session_id: {session_id!r}")
        return self.root / f"{session_id}.json"

    def list(self) -> list[dict]:
        """会话摘要列表（updated_at 倒序）；坏 JSON 跳过不报错"""
        items = []
        with self._lock:
            for p in sorted(self.root.glob("*.json")):
                data = self._read(p)
                if data is None:
                    continue
                items.append(self.summary(data))
        items.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
        return items

    def summary(self, data: dict) -> dict:
        """存储记录 → 列表摘要"""
        return {
            "id": data.get("id"),
            "title": data.get("title") or "",
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "message_count": len(data.get("messages") or []),
        }

    def _read(self, path: Path) -> dict | None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def load(self, session_id: str) -> dict | None:
        """读取会话记录（不存在/坏 JSON 返回 None）"""
        try:
            p = self._path(session_id)
        except ValueError:
            return None
        with self._lock:
            return self._read(p)

    def exists(self, session_id: str) -> bool:
        return self.load(session_id) is not None

    def create(self, session_id: str = None, title: str = "") -> dict:
        """新建空会话记录（不建 StockAgent）"""
        sid = session_id or _new_session_id()
        now = _now()
        data = {
            "id": sid,
            "title": title or "",
            "created_at": now,
            "updated_at": now,
            "protocol": "",
            "messages": [],
            "user_messages": [],
            "session_stocks": [],
            "session_topics": [],
        }
        self.save(data)
        return data

    def rename(self, session_id: str, title: str) -> dict | None:
        """改名（不存在返回 None）。

        读-改-写在同一 _lock 窗口内完成（与 persist_turn 同窗口），
        防长分析收尾 _persist 与并发 rename 互相覆盖丢失更新（m1）。
        """
        try:
            p = self._path(session_id)
        except ValueError:
            return None
        with self._lock:
            data = self._read(p)
            if data is None:
                return None
            data["title"] = title
            data = self._normalize(data)
            self._write(p, data)
            self._enforce_cap()
            return data

    def persist_turn(
        self,
        session_id: str,
        *,
        title: str,
        protocol,
        messages,
        user_messages,
        session_stocks,
        session_topics,
    ) -> None:
        """一轮对话收尾落盘：锁内读-改-写整表（与 rename 同窗口，防丢失更新）。

        - title：仅当文件现值仍是自动标题（is_auto_title）时才用本轮快照覆盖，
          否则以文件现值为准——长分析中手工 rename 不被收尾快照冲掉（m1）；
        - 侧表：agent 侧为空时保留文件现值，防回灌缺失被整表抹掉（M10 兜底）。
        """
        p = self._path(session_id)  # 非法 id 上抛 ValueError，不静默吞掉（m2）
        with self._lock:
            prev = self._read(p) or {}
            out_title = title if is_auto_title(prev) else (prev.get("title") or "")
            data = {
                "id": session_id,
                "title": out_title,
                "created_at": prev.get("created_at"),
                "protocol": protocol,
                "messages": list(messages or []),
                "user_messages": list(user_messages or [])
                or list(prev.get("user_messages") or []),
                "session_stocks": list(session_stocks or [])
                or list(prev.get("session_stocks") or []),
                "session_topics": list(session_topics or [])
                or list(prev.get("session_topics") or []),
            }
            self._write(p, self._normalize(data))
            self._enforce_cap()

    @staticmethod
    def _normalize(data: dict) -> dict:
        """补齐记录必备键并刷新 updated_at（save/rename/persist_turn 共用口径）"""
        data = dict(data)
        sid = data.get("id") or _new_session_id()
        data["id"] = sid
        if not data.get("created_at"):
            data["created_at"] = _now()
        data["updated_at"] = _now()
        data.setdefault("protocol", "")
        for key in ("messages", "user_messages", "session_stocks", "session_topics"):
            data.setdefault(key, [])
        return data

    def save(self, data: dict) -> None:
        """原子写整份记录，并按 updated_at 淘汰超出 cap 的最旧会话。

        非法 session_id 抛 ValueError（不再静默 return）——否则整轮对话正常 done
        但零落盘、同 id 在 GET/PATCH/DELETE 却 404，语义撕裂（m2）。
        """
        data = self._normalize(data)
        p = self._path(data["id"])
        with self._lock:
            self._write(p, data)
            self._enforce_cap()

    def _write(self, path: Path, data: dict) -> None:
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
                f.write("\n")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _enforce_cap(self) -> None:
        """超出 SESSION_CAP 时按 updated_at 淘汰最旧（坏文件一并清理）"""
        entries = []
        for p in self.root.glob("*.json"):
            data = self._read(p)
            if data is None:
                continue
            entries.append((data.get("updated_at") or "", p))
        if len(entries) <= SESSION_CAP:
            return
        entries.sort(key=lambda e: e[0])  # 最旧在前
        for _, p in entries[: len(entries) - SESSION_CAP]:
            try:
                p.unlink()
            except OSError:
                pass

    def delete(self, session_id: str) -> bool:
        """删除会话文件（不存在返回 False）"""
        try:
            p = self._path(session_id)
        except ValueError:
            return False
        with self._lock:
            if not p.exists():
                return False
            try:
                p.unlink()
                return True
            except OSError:
                return False


def _new_session_id() -> str:
    import uuid

    return str(uuid.uuid4())
