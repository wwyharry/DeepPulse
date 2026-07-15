"""实时行情多源管理器——故障切换、优先级调度、三态熔断"""

import logging

from deeppulse import config
from deeppulse.src.resilience import CircuitBreaker

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
    """管理多个实时行情数据源，提供故障切换和优先级调度

    使用三态熔断器（closed → open → half_open）保护下游数据源，
    阈值从 config.py 读取，支持配置化。
    """

    def __init__(self, priority: list[str] = None, timeout: float = 10.0):
        """
        Args:
            priority: 数据源名称列表，按优先级排序
            timeout: 单个数据源超时时间（秒），传递给数据源
        """
        self._priority = priority or DEFAULT_PRIORITY
        self._timeout = timeout
        self._sources: dict[str, RealtimeQuoteSource] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

    def _get_source(self, name: str) -> RealtimeQuoteSource:
        """懒加载获取数据源实例（传递 timeout 参数）"""
        if name not in self._sources:
            if name not in SOURCE_REGISTRY:
                raise ValueError(f"未知数据源: {name}")
            self._sources[name] = SOURCE_REGISTRY[name](timeout=self._timeout)
        return self._sources[name]

    def _get_breaker(self, name: str) -> CircuitBreaker:
        """获取或创建数据源对应的熔断器"""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                failure_threshold=config.CIRCUIT_FAILURE_THRESHOLD,
                recovery_timeout=config.CIRCUIT_RECOVERY_TIMEOUT,
                half_open_max_calls=config.CIRCUIT_HALF_OPEN_CALLS,
            )
        return self._breakers[name]

    def fetch_quote(self, code: str) -> RealtimeQuote | None:
        """获取单只股票实时行情，自动故障切换"""
        errors = []
        for source_name in self._priority:
            breaker = self._get_breaker(source_name)
            if not breaker.can_execute():
                continue
            try:
                source = self._get_source(source_name)
                quote = source.fetch_quote(code)
                if quote is not None:
                    breaker.record_success()
                    return quote
                # None 表示停牌/无数据，不算失败
                return None
            except Exception as e:
                breaker.record_failure()
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
            breaker = self._get_breaker(source_name)
            if not breaker.can_execute():
                continue

            try:
                source = self._get_source(source_name)
                quotes = source.fetch_quotes(remaining)
                if quotes:
                    breaker.record_success()
                    result.update(quotes)
                    remaining = [c for c in remaining if c not in result]
            except Exception as e:
                breaker.record_failure()
                logger.debug(f"数据源 {source_name} 批量查询失败: {e}")

        return result

    def cleanup(self):
        """清理所有数据源资源"""
        for source in self._sources.values():
            try:
                source.cleanup()
            except Exception:
                pass
