"""会话 Agent 池 —— 一会话一 agent + 一把 asyncio.Lock，懒建（构造即联网/建表）+ 回灌"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# 真 StockAgent 构造失败重试退避（照抄 tui/app.py:285-336）；测试注入的 factory 不重试
_RETRY_DELAYS = [3, 6]


@dataclass
class SessionRuntime:
    """单会话运行时：agent 实例、串行锁、运行中任务、删除墓碑"""

    session_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    agent: Any = None
    task: asyncio.Task | None = None
    # M2 删除墓碑：DELETE 置位后收尾 _persist 必须跳过，否则 DELETE 返回 ok 后文件被复活；
    # 同 id 新一轮对话开始时由 chat 清除（会话重建）
    deleted: bool = False


class AgentPool:
    """session_id → SessionRuntime（懒建 agent，绝不并发两路 chat）"""

    def __init__(self, setting: dict | None, agent_factory: Callable[[str], Any] | None, store=None):
        self.setting = setting or {}
        self._factory = agent_factory
        self._store = store
        self._runtimes: dict[str, SessionRuntime] = {}

    def runtime(self, session_id: str) -> SessionRuntime:
        """取会话运行时（无则建）。仅事件循环线程调用。"""
        rt = self._runtimes.get(session_id)
        if rt is None:
            rt = SessionRuntime(session_id=session_id)
            self._runtimes[session_id] = rt
        return rt

    def _build_agent(self, session_id: str):
        if self._factory is not None:
            return self._factory(session_id)
        from deeppulse.agent.agent import StockAgent

        return StockAgent(setting=self._setting_for_agent(), session_id=session_id)

    def _setting_for_agent(self) -> dict | None:
        """构造 StockAgent 用的 setting 副本：${ENV_VAR} 形式 api_key 在此展开。

        展开语义与 deeppulse.agent.client.resolve_api_key 一致（load_setting 同口径），
        否则 Web 路径会把字面 "${VAR}" 发给上游导致 401。
        """
        if not self.setting:
            return None
        import copy

        s = copy.deepcopy(self.setting)
        llm = s.get("llm")
        if isinstance(llm, dict) and llm.get("api_key") is not None:
            from deeppulse.agent.client import resolve_api_key

            llm["api_key"] = resolve_api_key(llm["api_key"])
        emb = s.get("embedding")
        # 嵌入 key 非必需（none/local 不用）：仅做宽松展开，解析失败保持原值由下游降级
        if isinstance(emb, dict) and isinstance(emb.get("api_key"), str):
            v = emb["api_key"].strip()
            if v.startswith("${") and v.endswith("}") and v[2:-1].strip():
                try:
                    from deeppulse.agent.client import resolve_api_key

                    emb["api_key"] = resolve_api_key(v)
                except ValueError:
                    pass
        return s

    async def get_agent(self, session_id: str) -> Any:
        """取（或懒建）会话 agent，并回灌存储历史。构造在线程池里跑，失败重试退避。"""
        rt = self.runtime(session_id)
        if rt.agent is not None:
            return rt.agent

        delays = _RETRY_DELAYS if self._factory is None else []
        last_err = None
        agent = None
        attempt = 0
        while True:
            try:
                agent = await asyncio.to_thread(self._build_agent, session_id)
                break
            except Exception as e:  # 构造可能因占位 key/网络失败
                last_err = e
                if attempt >= len(delays):
                    raise
                await asyncio.sleep(delays[attempt])
                attempt += 1
        if agent is None and last_err is not None:
            raise last_err

        rt.agent = agent
        self._restore(session_id, agent)
        return agent

    def _restore(self, session_id: str, agent: Any) -> None:
        """首建 agent 时回灌存储消息（[新 system] + stored[1:]）"""
        if agent is None or self._store is None:
            return
        try:
            from web.service import session_store as _ss_mod

            stored = self._store.load(session_id)
            if stored:
                _ss_mod.restore_agent_state(agent, stored)
        except Exception:
            pass

    def attach_store(self, store) -> None:
        """绑定会话存储（create_app 在 lifespan 注入）"""
        self._store = store

    def invalidate(self, session_id: str) -> None:
        """销毁缓存 agent（下一问重建）"""
        rt = self._runtimes.get(session_id)
        if rt is not None:
            rt.agent = None

    def invalidate_all(self) -> None:
        """llm/agent 配置变更后销毁全部缓存 agent"""
        for rt in self._runtimes.values():
            rt.agent = None

    def get_running_task(self, session_id: str):
        return self.runtime(session_id).task

    def set_running_task(self, session_id: str, task) -> None:
        self.runtime(session_id).task = task

    def cancel_running(self, session_id: str) -> bool:
        """取消会话运行中任务（无任务返回 False，幂等）"""
        rt = self._runtimes.get(session_id)
        if rt is None or rt.task is None or rt.task.done():
            return False
        rt.task.cancel()
        return True
