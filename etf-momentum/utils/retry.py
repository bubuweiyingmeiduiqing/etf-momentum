"""指数退避重试 + 熔断器"""
import time
import logging
import functools
import threading
from typing import Callable, Type, Tuple

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """熔断器：连续失败 N 次后拒绝请求，冷却后尝试半开。"""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = "CLOSED"   # CLOSED / OPEN / HALF_OPEN
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._state == "CLOSED":
                return False
            if self._state == "OPEN":
                if time.time() - self._last_failure_time >= self.cooldown_seconds:
                    self._state = "HALF_OPEN"
                    logger.info("熔断器进入半开状态，尝试恢复")
                    return False
                return True
            # HALF_OPEN → allow one trial
            return False

    def success(self):
        with self._lock:
            self._failure_count = 0
            self._state = "CLOSED"

    def failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                logger.warning("熔断器打开（连续失败 %d 次），冷却 %.0f 秒",
                               self._failure_count, self.cooldown_seconds)


def retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    circuit_breaker: CircuitBreaker = None,
):
    """指数退避重试装饰器，可选熔断器。"""
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if circuit_breaker and circuit_breaker.is_open:
                logger.error("熔断器开启，拒绝调用 %s", func.__name__)
                raise RuntimeError(f"Circuit breaker open for {func.__name__}")

            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    if circuit_breaker:
                        circuit_breaker.success()
                    return result
                except exceptions as e:
                    last_exc = e
                    if circuit_breaker:
                        circuit_breaker.failure()
                    if attempt == max_retries:
                        logger.error("%s 重试 %d 次后仍失败: %s", func.__name__, max_retries, e)
                        raise
                    delay = min(max_delay, base_delay * (backoff_factor ** (attempt - 1)))
                    logger.warning("%s 第 %d/%d 次失败: %s，%.1f 秒后重试",
                                   func.__name__, attempt, max_retries, e, delay)
                    time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator
