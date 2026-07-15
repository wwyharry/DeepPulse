"""Agent 实例池 — 管理多个会话的 Agent 实例，支持历史上下文恢复"""

from deeppulse.agent.agent import StockAgent
from deeppulse.agent.client import load_setting


class AgentPool:
    """管理多个 Agent 实例，每个会话一个"""

    def __init__(self):
        self._agents: dict[str, StockAgent] = {}
        self._setting = None

    def _get_setting(self) -> dict:
        if self._setting is None:
            try:
                self._setting = load_setting()
            except Exception:
                self._setting = {
                    "llm": {
                        "provider": "deepseek",
                        "base_url": "https://api.deepseek.com",
                        "api_key": "",
                        "model": "deepseek-chat",
                        "protocol": "openai",
                        "max_tokens": 16384,
                        "temperature": 0.3,
                    },
                    "agent": {"max_rounds": 10, "verbose": False},
                }
        return self._setting

    def get_or_create(self, session_id: str) -> StockAgent:
        """获取或创建 Agent 实例，支持历史上下文恢复"""
        if session_id not in self._agents:
            setting = self._get_setting()
            agent = StockAgent(
                setting=setting,
                verbose=False,
                session_id=session_id,
            )
            # 加载历史消息
            self._load_history(agent, session_id)
            self._agents[session_id] = agent
        return self._agents[session_id]

    def _load_history(self, agent: StockAgent, session_id: str):
        """从会话存储加载历史消息到 Agent 上下文"""
        from web.app.services.session_store import session_store

        messages = session_store.get_messages(session_id)
        if not messages:
            return

        # 将历史消息注入 Agent 的 messages 列表
        # 跳过 system 消息（Agent 已有自己的 system prompt）
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if not content:
                continue

            if role == "user":
                agent.messages.append({"role": "user", "content": content})
                agent._user_messages.append(content)
            elif role == "assistant":
                agent.messages.append({"role": "assistant", "content": content})

        # 从历史消息中提取股票和主题
        for msg in messages:
            if msg.get("role") == "user":
                agent._track_session_info(msg.get("content", ""))

    def remove(self, session_id: str):
        """移除 Agent 实例"""
        agent = self._agents.pop(session_id, None)
        if agent:
            agent.on_session_end()

    def list_sessions(self) -> list[str]:
        """列出所有活跃会话"""
        return list(self._agents.keys())

    def clear(self):
        """清除所有会话"""
        for agent in self._agents.values():
            agent.on_session_end()
        self._agents.clear()


# 全局单例
agent_pool = AgentPool()
