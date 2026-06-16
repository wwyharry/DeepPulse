"""共享的全局实例 - 延迟初始化，避免 import 时副作用"""

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

    import config as _config
    from src.query import StockQuery
    from src.realtime import RealtimeQuoteManager

    _query = StockQuery()
    _realtime_manager = RealtimeQuoteManager(
        priority=_config.REALTIME_SOURCES,
        timeout=_config.REALTIME_TIMEOUT,
    )

    from agent.memory import MemoryManager

    try:
        from agent.client import load_setting as _load_setting

        _setting = _load_setting()
    except Exception:
        _setting = None
    _memory = MemoryManager(setting=_setting)
    _memory.init_tables()

    from agent.strategy_loader import get_strategy_loader

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
