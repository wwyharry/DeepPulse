"""会话存储 — 本地 JSON 文件持久化对话历史"""

import json
import time
from pathlib import Path


class SessionStore:
    """本地文件存储会话历史"""

    def __init__(self, store_dir: Path = None):
        if store_dir is None:
            store_dir = Path(__file__).parent.parent.parent.parent / "web" / "data" / "sessions"
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.titles_file = self.store_dir / "_titles.json"
        self._titles = self._load_titles()

    def _load_titles(self) -> dict:
        """加载会话标题"""
        if self.titles_file.exists():
            try:
                with open(self.titles_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_titles(self):
        """保存会话标题"""
        with open(self.titles_file, "w", encoding="utf-8") as f:
            json.dump(self._titles, f, ensure_ascii=False, indent=2)

    def save_title(self, session_id: str, title: str):
        """保存会话标题"""
        self._titles[session_id] = title
        self._save_titles()

    def get_title(self, session_id: str) -> str:
        """获取会话标题"""
        return self._titles.get(session_id, "")

    def save_message(self, session_id: str, message: dict):
        """追加一条消息到会话"""
        path = self.store_dir / f"{session_id}.jsonl"
        message["_ts"] = time.time()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def get_messages(self, session_id: str) -> list[dict]:
        """获取会话的所有消息"""
        path = self.store_dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def get_first_user_message(self, session_id: str) -> str:
        """获取会话的第一条用户消息"""
        messages = self.get_messages(session_id)
        for msg in messages:
            if msg.get("role") == "user" and msg.get("content"):
                return msg["content"]
        return ""

    def list_sessions(self) -> list[dict]:
        """列出所有会话"""
        sessions = []
        for path in sorted(self.store_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            session_id = path.stem
            # 获取标题或第一条用户消息
            title = self.get_title(session_id)
            first_msg = ""
            if not title:
                first_msg = self.get_first_user_message(session_id)

            sessions.append(
                {
                    "id": session_id,
                    "title": title,
                    "first_message": first_msg,
                    "updated_at": path.stat().st_mtime,
                    "message_count": sum(1 for _ in open(path)),
                }
            )
        return sessions

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        path = self.store_dir / f"{session_id}.jsonl"
        if path.exists():
            path.unlink()
            # 删除标题
            self._titles.pop(session_id, None)
            self._save_titles()
            return True
        return False


# 全局单例
session_store = SessionStore()
