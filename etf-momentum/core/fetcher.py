"""Data fetcher - multi-source with automatic chain fallback."""

import logging
from datetime import datetime
from typing import Optional
import pandas as pd

from core.data_sources.manager import SourceManager

logger = logging.getLogger(__name__)


class DataFetcher:
    """Market data fetcher using multi-source chain fallback."""

    def __init__(self, config: dict, database=None):
        fc = config.get("fetcher", {})
        self.symbols = fc.get("symbols", [])
        self.db = database
        self.interval_minutes = fc.get("interval_minutes", 5)
        self.start_date = fc.get("start_date", "2024-01-01")
        self.mock_fallback = fc.get("use_mock_fallback", False)
        self.source_manager = SourceManager(mock_fallback=self.mock_fallback)

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        """Fetch real-time quote via best available source."""
        return self.source_manager.fetch_realtime(symbol)

    def fetch_all_realtime(self) -> list:
        """Fetch all monitored symbols."""
        results = []
        failed = []
        for sym in self.symbols:
            quote = self.fetch_realtime(sym)
            if quote:
                results.append(quote)
                if self.db:
                    try:
                        self.db.insert_quote(sym, quote)
                    except Exception as e:
                        logger.error("DB insert failed for %s: %s", sym, e)
            else:
                failed.append(sym)
        if failed:
            logger.warning("Failed symbols: %s", ", ".join(failed))
        logger.info("Fetched %d/%d symbols", len(results), len(self.symbols))
        return results

    def fetch_history(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """Fetch historical daily data."""
        if start_date is None:
            start_date = self.start_date
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        return self.source_manager.fetch_history(symbol, start_date, end_date)

    def fetch_all_history(self) -> dict:
        """Fetch history for all symbols and save to DB."""
        result = {}
        for sym in self.symbols:
            df = self.fetch_history(sym)
            if not df.empty:
                result[sym] = df
                if self.db:
                    for _, row in df.iterrows():
                        try:
                            date_str = str(row["date"])[:10]
                            o = row.get("open")
                            h = row.get("high")
                            l = row.get("low")
                            c = row.get("close")
                            if not self.validate_price(sym, date_str, o, h, l, c):
                                continue
                            self.db.upsert_daily_summary(sym, {
                                "date": date_str,
                                "open": o, "high": h, "low": l, "close": c,
                                "volume": row.get("volume"),
                                "change_pct": row.get("change_pct"),
                            })
                        except Exception as e:
                            logger.error("DB history insert failed for %s: %s", sym, e)
        logger.info("Synced %d/%d symbols history", len(result), len(self.symbols))
        return result



    @staticmethod
    def validate_price(symbol: str, date: str, o: float, h: float, l: float, c: float) -> bool:
        """Validate OHLC data is sane. Returns True if valid."""
        if c is None or c <= 0:
            logger.warning("Price validation FAILED: %s %s close=%s (zero/negative)", symbol, date, c)
            return False
        if c < 0.01 or c > 500:
            logger.warning("Price validation FAILED: %s %s close=%s (out of range 0.01-500)", symbol, date, c)
            return False
        if h is not None and l is not None and h < l:
            logger.warning("Price validation FAILED: %s %s high=%s < low=%s", symbol, date, h, l)
            return False
        if h is not None and c > h * 1.2:
            logger.warning("Price validation SUSPICIOUS: %s %s close=%s far above high=%s", symbol, date, c, h)
        if l is not None and c < l * 0.8:
            logger.warning("Price validation SUSPICIOUS: %s %s close=%s far below low=%s", symbol, date, c, l)
        return True

    @property
    def config(self) -> dict:
        """Backward-compatible config access."""
        return {"interval_minutes": self.interval_minutes, "start_date": self.start_date}

    def is_trade_day(self) -> bool:
        now = datetime.now()
        return now.weekday() < 5

    def get_source_stats(self) -> list:
        return self.source_manager.get_stats()

    def reset_sources(self):
        self.source_manager.reset_circuits()
