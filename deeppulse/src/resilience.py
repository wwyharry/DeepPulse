"""数据源韧性基础设施 — 重试策略、三态熔断器、健康检查"""

import logging
import random
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


class RetryPolicy:
    """可配置的指数退避重试策略

    Args:
        max_retries: 最大重试次数（不含首次尝试）
        base_delay: 基础延迟秒数
        max_delay: 最大延迟秒数
        backoff_factor: 退避因子（每次重试延迟乘以此值）
        retryable_errors: 可重试的异常类型元组
        non_retryable_errors: 不可重试的异常类型（匹配时立即失败，不重试）
        jitter: 是否添加随机抖动，防止惊群
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
        retryable_errors: tuple = (Exception,),
        non_retryable_errors: tuple = (),
        jitter: bool = True,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.retryable_errors = retryable_errors
        self.non_retryable_errors = non_retryable_errors
        self.jitter = jitter

    def _calc_delay(self, attempt: int) -> float:
        """计算第 attempt 次重试的延迟时间"""
        delay = self.base_delay * (self.backoff_factor**attempt)
        delay = min(delay, self.max_delay)
        if self.jitter:
            delay += random.uniform(0, delay * 0.3)
        return delay

    def _is_non_retryable(self, error: Exception) -> bool:
        """判断错误是否不可重试（连接被拒、协议错误等）"""
        if self.non_retryable_errors and isinstance(error, self.non_retryable_errors):
            return True
        # 检查错误消息中的关键词
        msg = str(error).lower()
        non_retryable_keywords = [
            "remote disconnected",
            "connection refused",
            "connection reset",
            "connection aborted",
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo failed",
        ]
        return any(kw in msg for kw in non_retryable_keywords)

    def execute(self, fn, *args, **kwargs):
        """执行函数，失败时按策略重试

        Returns:
            fn 的返回值

        Raises:
            最后一次重试仍失败时抛出最后一个异常
        """
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except self.retryable_errors as e:
                last_err = e
                # 不可重试的错误立即失败
                if self._is_non_retryable(e):
                    logger.warning(f"[快速失败] {fn.__name__ if hasattr(fn, '__name__') else fn} 遇到不可重试错误: {e}")
                    raise
                if attempt < self.max_retries:
                    delay = self._calc_delay(attempt)
                    logger.warning(
                        f"[重试] {fn.__name__ if hasattr(fn, '__name__') else fn} "
                        f"第{attempt + 1}次失败: {e}，{delay:.1f}秒后重试"
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"[重试] {fn.__name__ if hasattr(fn, '__name__') else fn} 重试{self.max_retries}次后仍失败: {e}"
                    )
        raise last_err


class CircuitState(Enum):
    """熔断器状态"""

    CLOSED = "closed"  # 正常状态，允许请求通过
    OPEN = "open"  # 熔断状态，拒绝所有请求
    HALF_OPEN = "half_open"  # 半开状态，允许少量试探请求


class CircuitBreaker:
    """三态熔断器

    状态转换：
        CLOSED --(连续失败达阈值)--> OPEN
        OPEN --(恢复超时)--> HALF_OPEN
        HALF_OPEN --(试探成功)--> CLOSED
        HALF_OPEN --(试探失败)--> OPEN

    Args:
        failure_threshold: 触发熔断的连续失败次数
        recovery_timeout: 熔断恢复超时秒数
        half_open_max_calls: 半开状态最大试探调用数
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """获取当前状态（自动检查 OPEN → HALF_OPEN 转换）"""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._success_count = 0
                    logger.info("[熔断器] 恢复超时，进入半开状态")
            return self._state

    def can_execute(self) -> bool:
        """是否允许执行请求"""
        state = self.state  # 触发自动转换检查
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            with self._lock:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
        return False  # OPEN

    def record_success(self):
        """记录成功"""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_max_calls:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info("[熔断器] 半开状态试探成功，恢复正常")
            else:
                self._failure_count = 0

    def record_failure(self):
        """记录失败"""
        with self._lock:
            self._last_failure_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                # 半开状态失败，立即重新熔断
                self._state = CircuitState.OPEN
                self._failure_count = 0
                self._success_count = 0
                logger.warning(f"[熔断器] 半开状态试探失败，重新熔断 {self.recovery_timeout}秒")
            else:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(f"[熔断器] 连续失败{self._failure_count}次，熔断 {self.recovery_timeout}秒")

    def reset(self):
        """手动重置熔断器到正常状态"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0
