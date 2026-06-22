"""Yahoo Finance data source - HTTP chart API (matching etf_report.py approach)."""

import json, logging, time, requests
from datetime import datetime
from typing import Optional
import pandas as pd
from .base import DataSource

logger = logging.getLogger(__name__)

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

SYMBOL_MAP = {
    "510050": "510050.SS", "510300": "510300.SS", "510500": "510500.SS",
    "510880": "510880.SS", "510890": "510890.SS", "512880": "512880.SS",
    "588000": "588000.SS", "159915": "159915.SZ", "511010": "511010.SS",
    "513100": "513100.SS", "513520": "513520.SS",
}


class YFinanceSource(DataSource):
    """Yahoo Finance - HTTP chart API, works globally."""

    def __init__(self):
        super().__init__("yfinance", priority=5)

    def _to_yahoo(self, symbol: str) -> str:
        return SYMBOL_MAP.get(symbol, f"{symbol}.SS" if symbol[0] in "569" else f"{symbol}.SZ")

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        """Fetch via Yahoo chart API (last 2 days)."""
        try:
            ys = self._to_yahoo(symbol)
            end_ts = int(time.time()) + 86400
            start_ts = end_ts - 5 * 86400
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ys}"
            params = {
                "period1": str(start_ts), "period2": str(end_ts),
                "interval": "1d", "events": "history",
            }
            resp = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if not result:
                return None

            ts = result.get("timestamp") or []
            quote = ((result.get("indicators", {}).get("quote") or [{}])[0])
            closes = quote.get("close") or []
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            volumes = quote.get("volume") or []

            if not closes or closes[-1] is None:
                return None

            idx = len(closes) - 1
            prev_idx = idx - 1 if idx > 0 else 0
            chg = 0.0
            if closes[prev_idx] and closes[prev_idx] != 0:
                chg = round((closes[idx] - closes[prev_idx]) / closes[prev_idx] * 100, 2)

            self.record_success()
            return {
                "symbol": symbol, "name": symbol,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(opens[idx], 4) if opens[idx] else None,
                "high": round(highs[idx], 4) if highs[idx] else None,
                "low": round(lows[idx], 4) if lows[idx] else None,
                "close": round(closes[idx], 4),
                "volume": int(volumes[idx]) if volumes[idx] else None,
                "amount": None, "change_pct": chg,
                "source": self.name,
            }
        except Exception as e:
            self.record_failure()
            logger.debug("yfinance realtime %s: %s", symbol, e)
            return None

    def fetch_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch via Yahoo chart API using timestamp range."""
        try:
            ys = self._to_yahoo(symbol)
            # Convert dates to timestamps
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            period1 = int(start_dt.timestamp())
            period2 = int(end_dt.timestamp()) + 86400

            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ys}"
            params = {
                "period1": str(period1), "period2": str(period2),
                "interval": "1d", "events": "history",
            }
            resp = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if not result:
                return pd.DataFrame()

            ts = result.get("timestamp") or []
            quote = ((result.get("indicators", {}).get("quote") or [{}])[0])
            opens = quote.get("open") or []
            closes = quote.get("close") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            volumes = quote.get("volume") or []

            rows = []
            for i, t in enumerate(ts):
                c = closes[i] if i < len(closes) else None
                if c is None:
                    continue
                prev = closes[i-1] if i > 0 and i-1 < len(closes) else None
                chg = round((c - prev) / prev * 100, 2) if prev and prev != 0 else None
                rows.append({
                    "symbol": symbol,
                    "date": datetime.fromtimestamp(t).strftime("%Y-%m-%d"),
                    "open": round(opens[i], 4) if i < len(opens) and opens[i] else None,
                    "high": round(highs[i], 4) if i < len(highs) and highs[i] else None,
                    "low": round(lows[i], 4) if i < len(lows) and lows[i] else None,
                    "close": round(c, 4),
                    "volume": volumes[i] if i < len(volumes) else None,
                    "amount": None,
                    "change_pct": chg,
                })

            self.record_success()
            df = pd.DataFrame(rows)
            return df[["symbol", "date", "open", "high", "low", "close", "volume", "amount", "change_pct"]]
        except Exception as e:
            self.record_failure()
            logger.debug("yfinance history %s: %s", symbol, e)
            return pd.DataFrame()
