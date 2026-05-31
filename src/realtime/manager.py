"""实时行情多源管理器——故障切换、优先级调度"""

import logging
import time

from .base import RealtimeQuote, RealtimeQuoteSource
from .eastmoney_source import EastMoneyRealtimeSource
from .sina_source import SinaRealtimeSource

logger = logging.getLogger(__name__)

# 数据源注册表
SOURCE_REGISTRY: dict[str, type[RealtimeQuoteSource]] = {
    "sina": SinaRealtimeSource,
    "eastmoney": EastMoneyRealtimeSource,
}

# 默认优先级顺序
DEFAULT_PRIORITY = ["sina", "eastmoney"]


class RealtimeQuoteManager:
    """管理多个实时行情数据源，提供故障切换和优先级调度"""

    def __init__(self, priority: list[str] = None, timeout: float = 10.0):
        """
        Args:
            priority: 数据源名称列表，按优先级排序
            timeout: 单个数据源超时时间（秒）
        """
        self._priority = priority or DEFAULT_PRIORITY
        self._timeout = timeout
        self._sources: dict[str, RealtimeQuoteSource] = {}
        self._failure_counts: dict[str, int] = {}
        self._circuit_open_until: dict[str, float] = {}

    def _get_source(self, name: str) -> RealtimeQuoteSource:
        """懒加载获取数据源实例"""
        if name not in self._sources:
            if name not in SOURCE_REGISTRY:
                raise ValueError(f"未知数据源: {name}")
            self._sources[name] = SOURCE_REGISTRY[name]()
            self._failure_counts[name] = 0
        return self._sources[name]

    def _is_available(self, name: str) -> bool:
        """检查数据源是否可用（未被熔断）"""
        now = time.time()
        if name in self._circuit_open_until:
            if now < self._circuit_open_until[name]:
                return False
            # 熔断恢复：重置计数
            self._failure_counts[name] = 0
            del self._circuit_open_until[name]
        return True

    def _record_success(self, name: str):
        """记录成功，重置失败计数"""
        self._failure_counts[name] = 0

    def _record_failure(self, name: str):
        """记录失败，达到阈值触发熔断"""
        self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
        if self._failure_counts[name] >= 3:
            self._circuit_open_until[name] = time.time() + 60
            logger.warning(f"数据源 {name} 连续失败{self._failure_counts[name]}次，熔断60秒")

    def fetch_quote(self, code: str) -> RealtimeQuote | None:
        """获取单只股票实时行情，自动故障切换"""
        errors = []
        for source_name in self._priority:
            if not self._is_available(source_name):
                continue
            try:
                source = self._get_source(source_name)
                quote = source.fetch_quote(code)
                if quote is not None:
                    self._record_success(source_name)
                    return quote
            except Exception as e:
                self._record_failure(source_name)
                errors.append(f"[{source_name}] {e}")
                logger.debug(f"数据源 {source_name} 查询 {code} 失败: {e}")

        if errors:
            logger.warning(f"所有数据源查询 {code} 失败: {'; '.join(errors)}")
        return None

    def fetch_quotes(self, codes: list[str]) -> dict[str, RealtimeQuote]:
        """批量获取实时行情，自动故障切换

        策略：先用主源批量获取，遗漏的用备用源补充
        """
        if not codes:
            return {}

        remaining = list(codes)
        result = {}

        for source_name in self._priority:
            if not remaining:
                break
            if not self._is_available(source_name):
                continue

            try:
                source = self._get_source(source_name)
                quotes = source.fetch_quotes(remaining)
                if quotes:
                    self._record_success(source_name)
                    result.update(quotes)
                    remaining = [c for c in remaining if c not in result]
            except Exception as e:
                self._record_failure(source_name)
                logger.debug(f"数据源 {source_name} 批量查询失败: {e}")

        return result
