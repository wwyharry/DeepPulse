"""DeepPulse Web UI 后端（FastAPI + SSE 流式对话）"""

__all__ = ["create_app"]


def __getattr__(name):
    # 惰性导出 create_app，避免 import web 就拉起 FastAPI 依赖
    if name == "create_app":
        from web.app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
