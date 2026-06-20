"""Yahoo Finance data source - most reliable globally."""

import logging
from datetime import datetime
from typing import Optional
import pandas as pd
from .base import DataSource

logger = logging.getLogger(__name__)


class YFinanceSource(DataSource):
    """Yahoo Finance - works from anywhere, good for cross-border ETFs."""

    # ETF code -> Yahoo symbol mapping
    SYMBOL_MAP = {
        "510050": "510050.SS", "510300": "510300.SS", "510500": "510500.SS",
        "510880": "510880.SS", "510890": "510890.SS", "512880": "512880.SS",
        "588000": "588000.SS", "159915": "159915.SZ", "511010": "511010.SS",
        "513100": "513100.SS", "513520": "513520.SS",
    }

    def __init__(self):
        super().__init__("yfinance", priority=10)

    def _to_yahoo(self, symbol: str) -> str:
        if symbol in self.SYMBOL_MAP:
            return self.SYMBOL_MAP[symbol]
        if len(symbol) == 6:
            if symbol.startswith(("5", "6", "9")):
                return f"{symbol}.SS"
            return f"{symbol}.SZ"
        return symbol

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        try:
            import yfinance as yf
            ys = self._to_yahoo(symbol)
            ticker = yf.Ticker(ys)
            info = ticker.history(period="2d")
            if info.empty:
                return None
            row = info.iloc[-1]
            prev = info.iloc[-2] if len(info) >= 2 else row
            now = datetime.now()
            close = round(float(row["Close"]), 4)
            prev_close = round(float(prev["Close"]), 4)
            chg = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0
            self.record_success()
            return {
                "symbol": symbol, "name": symbol, "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(float(row["Open"]), 4), "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4), "close": close,
                "volume": int(row["Volume"]), "amount": None, "change_pct": chg,
                "source": self.name,
            }
        except Exception as e:
            self.record_failure()
            logger.debug("yfinance fetch_realtime %s: %s", symbol, e)
            return None

    def fetch_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            import yfinance as yf
            ys = self._to_yahoo(symbol)
            ticker = yf.Ticker(ys)
            df = ticker.history(start=start_date, end=end_date)
            if df.empty:
                return pd.DataFrame()
            df = df.reset_index()
            df = df.rename(columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df["symbol"] = symbol
            self.record_success()
            return df[["symbol", "date", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            self.record_failure()
            logger.debug("yfinance fetch_history %s: %s", symbol, e)
            return pd.DataFrame()
