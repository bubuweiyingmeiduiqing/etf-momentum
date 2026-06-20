"""Abstract base class for market data sources."""

from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd
from utils.retry import CircuitBreaker


class DataSource(ABC):
    """Base class for all market data sources."""

    def __init__(self, name: str, priority: int = 100):
        self.name = name
        self.priority = priority
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120)
        self._call_count = 0
        self._fail_count = 0

    @abstractmethod
    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        """Fetch real-time quote. Returns dict or None."""

    @abstractmethod
    def fetch_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch historical daily data."""

    def is_available(self) -> bool:
        """Check if this source is currently usable (circuit breaker not open)."""
        return not self.circuit_breaker.is_open

    def record_success(self):
        self.circuit_breaker.success()
        self._call_count += 1

    def record_failure(self):
        self.circuit_breaker.failure()
        self._fail_count += 1

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "priority": self.priority,
            "calls": self._call_count,
            "fails": self._fail_count,
            "available": self.is_available(),
        }

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None or val == "-" or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
