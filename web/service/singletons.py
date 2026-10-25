"""进程内单例容器 —— 仿 tools/_shared.ensure_initialized()，manager 构造即 DDL 勿每请求新建"""

from pathlib import Path


class ServiceSingletons:
    """RealtimeQuoteManager / WatchlistManager / TradeJournal / StockQuery / MemoryManager

    每个 app 实例一份（lifespan 构建一次）：
    - RealtimeQuoteManager 熔断器挂实例，每请求新建 = 熔断反复重置；
    - WatchlistManager/TradeJournal 构造即写库 DDL，每请求新建有开销；
    - db_path 可注入（测试用 tmp 库，绝不落真实库）。
    """

    def __init__(self, db_path: Path | None = None, setting: dict | None = None):
        self.db_path = Path(db_path) if db_path else None
        self.setting = setting or {}
        self._realtime = None
        self._watchlist = None
        self._journal = None
        self._query = None
        self._memory = None

    def init(self) -> None:
        """同步初始化全部单例（含 DuckDB DDL，阻塞 —— 调用方须 to_thread）"""
        from deeppulse import config as _config
        from deeppulse.agent.journal import TradeJournal
        from deeppulse.agent.watchlist import WatchlistManager
        from deeppulse.src.query import StockQuery
        from deeppulse.src.realtime import RealtimeQuoteManager

        self._realtime = RealtimeQuoteManager(
            priority=_config.REALTIME_SOURCES,
            timeout=_config.REALTIME_TIMEOUT,
        )
        self._watchlist = WatchlistManager(db_path=self.db_path)
        self._journal = TradeJournal(db_path=self.db_path)
        self._query = StockQuery(db_path=self.db_path)
        self._init_memory()

    def _init_memory(self) -> None:
        """记忆系统单例（失败不致命，/memory 命令会降级）"""
        try:
            from deeppulse.agent.memory import MemoryManager

            self._memory = MemoryManager(db_path=self.db_path, setting=self.setting or None)
            self._memory.init_tables()
        except Exception:
            self._memory = None

    @property
    def realtime(self):
        return self._realtime

    @property
    def watchlist(self):
        return self._watchlist

    @property
    def journal(self):
        return self._journal

    @property
    def query(self):
        return self._query

    @property
    def memory(self):
        return self._memory

    def fetch_quotes(self, codes: list) -> dict:
        """批量实时行情；任一失败返回已有部分（缺码由调用方按 None 处理）"""
        if not codes or self._realtime is None:
            return {}
        try:
            return self._realtime.fetch_quotes(list(codes)) or {}
        except Exception:
            return {}
