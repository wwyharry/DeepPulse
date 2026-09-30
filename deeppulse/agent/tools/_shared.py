"""共享的全局实例与工具函数 - 延迟初始化，避免 import 时副作用"""

from datetime import date, datetime

# 成交量单位：数据库存储为股，展示时转为手（1手=100股）
LOTS_PER_SHARE = 100  # 1手 = 100股

_query = None
_realtime_manager = None
_memory = None
_strategy_loader = None
_initialized = False


def ensure_initialized():
    """惰性初始化全局实例"""
    global _query, _realtime_manager, _memory, _strategy_loader, _initialized
    if _initialized:
        return

    from deeppulse import config as _config
    from deeppulse.src.query import StockQuery
    from deeppulse.src.realtime import RealtimeQuoteManager

    _query = StockQuery()
    _realtime_manager = RealtimeQuoteManager(
        priority=_config.REALTIME_SOURCES,
        timeout=_config.REALTIME_TIMEOUT,
    )

    from deeppulse.agent.memory import MemoryManager

    try:
        from deeppulse.agent.client import load_setting as _load_setting

        _setting = _load_setting()
    except Exception:
        _setting = None
    _memory = MemoryManager(setting=_setting)
    _memory.init_tables()

    from deeppulse.agent.strategy_loader import get_strategy_loader

    _strategy_loader = get_strategy_loader()
    _initialized = True


def get_query():
    ensure_initialized()
    return _query


def get_realtime_manager():
    ensure_initialized()
    return _realtime_manager


def get_memory():
    ensure_initialized()
    return _memory


def get_strategy_loader():
    ensure_initialized()
    return _strategy_loader


def shares_to_lots(volume) -> int | None:
    """将成交量从股转换为手（1手=100股），向下取整"""
    if volume is None:
        return None
    try:
        return int(float(volume) // LOTS_PER_SHARE)
    except (ValueError, TypeError):
        return None


def convert_volume_in_records(records: list[dict], volume_key: str = "volume") -> list[dict]:
    """将记录列表中的成交量字段从股转为手（原地修改，返回原列表）"""
    for r in records:
        if volume_key in r and r[volume_key] is not None:
            r[volume_key] = shares_to_lots(r[volume_key])
    return records


def parse_positive_int(value, name: str, default=None) -> int:
    """解析为正整数入参（days/limit/max_days 等）。

    None 且提供 default 时返回 default；其余非法值（bool、0、负数、非数字、非整数浮点）
    抛 ValueError，由 agent._run_tool 转成 error JSON 返回给 LLM 自纠。
    """
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"{name} 不能为 None")
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须为正整数，收到 {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} 必须为正整数，收到 {value!r}")
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须为正整数，收到 {value!r}") from None
    if v <= 0:
        raise ValueError(f"{name} 必须为正整数，收到 {value!r}")
    return v


def norm_trade_date(value) -> str | None:
    """将常见日期写法归一化为 YYYYMMDD。

    None/空串视为今天（保持"缺省=当日"契约）；无法解析返回 None（由调用方组装错误信息）。
    支持 YYYYMMDD、YYYY-MM-DD、YYYY/MM/DD、YYYY.MM.DD、YYYY年MM月DD日，忽略尾部时间。
    """
    if value is None:
        return date.today().strftime("%Y%m%d")
    s = str(value).strip()
    if not s:
        return date.today().strftime("%Y%m%d")
    s = s.split(" ")[0].split("T")[0]
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return None
