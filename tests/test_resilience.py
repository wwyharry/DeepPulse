"""韧性基础设施测试 — RetryPolicy, CircuitBreaker"""

import time
from unittest.mock import MagicMock

import pytest

from deeppulse.src.resilience import CircuitBreaker, CircuitState, RetryPolicy

# ────────────────────── RetryPolicy 测试 ──────────────────────


class TestRetryPolicy:
    """重试策略测试"""

    def test_first_try_success(self):
        """首次成功不重试"""
        policy = RetryPolicy(max_retries=3)
        fn = MagicMock(return_value="ok")
        result = policy.execute(fn)
        assert result == "ok"
        assert fn.call_count == 1

    def test_retry_then_success(self):
        """前两次失败，第三次成功"""
        policy = RetryPolicy(max_retries=3, base_delay=0.01, jitter=False)
        fn = MagicMock(side_effect=[ValueError("fail"), ValueError("fail"), "ok"])
        result = policy.execute(fn)
        assert result == "ok"
        assert fn.call_count == 3

    def test_all_retries_exhausted(self):
        """所有重试用尽，抛出最后一个异常"""
        policy = RetryPolicy(max_retries=2, base_delay=0.01, jitter=False)
        fn = MagicMock(side_effect=ValueError("always fail"))
        with pytest.raises(ValueError, match="always fail"):
            policy.execute(fn)
        assert fn.call_count == 3  # 首次 + 2次重试

    def test_only_catches_retryable_errors(self):
        """只捕获指定的异常类型"""
        policy = RetryPolicy(max_retries=3, base_delay=0.01, retryable_errors=(ValueError,))
        fn = MagicMock(side_effect=TypeError("wrong type"))
        with pytest.raises(TypeError):
            policy.execute(fn)
        assert fn.call_count == 1  # 不重试

    def test_exponential_backoff_timing(self):
        """指数退避延迟递增"""
        policy = RetryPolicy(max_retries=3, base_delay=1.0, backoff_factor=2.0, jitter=False)
        delays = [policy._calc_delay(i) for i in range(3)]
        assert delays[0] == 1.0  # 1.0 * 2^0
        assert delays[1] == 2.0  # 1.0 * 2^1
        assert delays[2] == 4.0  # 1.0 * 2^2

    def test_max_delay_cap(self):
        """延迟不超过 max_delay"""
        policy = RetryPolicy(max_retries=5, base_delay=1.0, max_delay=5.0, backoff_factor=10.0, jitter=False)
        delay = policy._calc_delay(5)  # 1.0 * 10^5 = 100000, 但 cap 到 5.0
        assert delay == 5.0

    def test_jitter_adds_randomness(self):
        """抖动使延迟有随机性"""
        policy = RetryPolicy(max_retries=1, base_delay=10.0, jitter=True)
        delays = [policy._calc_delay(0) for _ in range(20)]
        # 所有延迟应在 [10.0, 13.0) 范围内（base + up to 30% jitter）
        assert all(10.0 <= d < 13.0 for d in delays)
        # 不应全部相同
        assert len(set(delays)) > 1

    def test_no_jitter(self):
        """无抖动时延迟固定"""
        policy = RetryPolicy(max_retries=1, base_delay=5.0, jitter=False)
        assert policy._calc_delay(0) == 5.0

    def test_passes_args_and_kwargs(self):
        """正确传递参数"""
        policy = RetryPolicy(max_retries=0)
        fn = MagicMock(return_value="ok")
        result = policy.execute(fn, "a", "b", key="val")
        assert result == "ok"
        fn.assert_called_once_with("a", "b", key="val")


# ────────────────────── CircuitBreaker 测试 ──────────────────────


class TestCircuitBreaker:
    """三态熔断器测试"""

    def test_initial_state_closed(self):
        """初始状态为 CLOSED"""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_failure_count_increments(self):
        """失败计数递增"""
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        """成功重置失败计数"""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # 重置
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # 只有1次失败，未达阈值

    def test_open_state_blocks_requests(self):
        """OPEN 状态拒绝请求"""
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_recovery_to_half_open(self):
        """超时后自动转为 HALF_OPEN"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.15)  # 等待恢复超时
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_limited_calls(self):
        """HALF_OPEN 状态只允许有限次数的试探"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, half_open_max_calls=1)
        cb.record_failure()
        cb.record_failure()

        time.sleep(0.15)
        assert cb.can_execute() is True  # 第一次试探放行
        assert cb.can_execute() is False  # 第二次被拒绝

    def test_half_open_success_closes(self):
        """HALF_OPEN 状态试探成功 → CLOSED"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, half_open_max_calls=1)
        cb.record_failure()
        cb.record_failure()

        time.sleep(0.15)
        cb.can_execute()  # 消耗试探名额
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True  # 恢复正常

    def test_half_open_failure_reopens(self):
        """HALF_OPEN 状态试探失败 → OPEN"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, half_open_max_calls=1)
        cb.record_failure()
        cb.record_failure()

        time.sleep(0.15)
        cb.can_execute()  # 消耗试探名额
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_reset(self):
        """手动重置"""
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_state_transitions_full_cycle(self):
        """完整状态循环：CLOSED → OPEN → HALF_OPEN → CLOSED"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)

        # CLOSED
        assert cb.state == CircuitState.CLOSED

        # CLOSED → OPEN
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # OPEN → HALF_OPEN
        time.sleep(0.08)
        assert cb.state == CircuitState.HALF_OPEN

        # HALF_OPEN → CLOSED
        cb.can_execute()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_thread_safety(self):
        """多线程并发访问不崩溃"""
        import threading

        cb = CircuitBreaker(failure_threshold=10)
        errors = []

        def worker():
            try:
                for _ in range(100):
                    if cb.can_execute():
                        cb.record_failure()
                    cb.record_success()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
