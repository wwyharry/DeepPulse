from datetime import datetime
from agent.tools.decorator import tool


@tool(
    "获取当前本地时间"
)
def get_current_time() -> str:
    """获取当前本地时间"""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")