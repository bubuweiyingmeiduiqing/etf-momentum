"""Data source manager - chains multiple sources with automatic fallback."""

import logging, time
from typing import Optional
import pandas as pd
from .base import DataSource
from .yfinance_source import YFinanceSource
from .eastmoney_source import EastMoneySource
from .akshare_source import AKShareSource
from .sina_source import SinaSource

logger = logging.getLogger(__name__)


class SourceManager:
    """Orchestrates multiple data sources with priority-based fallback."""

    def __init__(self, mock_fallback: bool = False):
        self.sources = []
        self.mock_fallback = mock_fallback
        self._init_sources()

    def _init_sources(self):
        """Initialize all available sources in priority order."""
        self.sources = [
            EastMoneySource(),   # priority 20 - lightweight HTTP, good from overseas
            YFinanceSource(),    # priority 10 - most reliable globally
            AKShareSource(),     # priority 30 - best data but may have issues
            SinaSource(),        # priority 40 - last HTTP fallback
        ]
        self.sources.sort(key=lambda s: s.priority)
        logger.info("Data sources initialized: %s", [s.name for s in self.sources])

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        """Try all sources in priority order until one succeeds."""
        for source in self.sources:
            if not source.is_available():
                logger.debug("Source %s unavailable (circuit open), skipping", source.name)
                continue
            logger.debug("Trying %s for %s", source.name, symbol)
            result = source.fetch_realtime(symbol)
            if result:
                logger.debug("Source %s succeeded for %s", source.name, symbol)
                return result
            time.sleep(0.3)  # small delay between sources

        # All sources failed
        if self.mock_fallback:
            logger.warning("All sources failed for %s, using mock data", symbol)
            return self._mock_quote(symbol)
        logger.error("All sources failed for %s", symbol)
        return None

    def fetch_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Try all sources for historical data (no circuit breaker for history)."""
        import time as t
        for source in self.sources:
            logger.debug("Trying %s for %s history", source.name, symbol)
            for attempt in range(1, 4):
                try:
                    df = source.fetch_history(symbol, start_date, end_date)
                    if not df.empty:
                        logger.debug("Source %s returned %d rows for %s", source.name, len(df), symbol)
                        return df
                    if attempt < 3:
                        t.sleep(2 * attempt)
                except Exception as e:
                    logger.debug("Source %s attempt %d: %s", source.name, attempt, e)
                    if attempt < 3:
                        t.sleep(2 * attempt)
            logger.debug("Source %s exhausted for %s", source.name, symbol)
        logger.error("All sources failed for %s history", symbol)
        return pd.DataFrame()

    def get_stats(self) -> list:
        """Return stats for all sources."""
        return [s.stats for s in self.sources]

    def reset_circuits(self):
        """Force-reset all circuit breakers (for emergency recovery)."""
        for s in self.sources:
            s.circuit_breaker.success()

    @staticmethod
    def _mock_quote(symbol: str) -> dict:
        import random
        from datetime import datetime
        now = datetime.now()
        base = 1.0 + random.random() * 2
        chg = (random.random() - 0.5) * 0.04
        return {
            "symbol": symbol, "name": f"MOCK-{symbol}",
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(base, 4), "high": round(base * 1.01, 4),
            "low": round(base * 0.99, 4), "close": round(base + chg, 4),
            "volume": random.randint(100000, 10000000),
            "amount": random.randint(1000000, 100000000),
            "change_pct": round(chg / base * 100, 2),
            "source": "mock",
        }
