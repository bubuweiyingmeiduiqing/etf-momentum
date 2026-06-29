"""EastMoney HTTP API - lightweight, often accessible from overseas."""

import json, logging, re
from datetime import datetime
from typing import Optional
import pandas as pd
import requests
from .base import DataSource

logger = logging.getLogger(__name__)


class EastMoneySource(DataSource):
    """EastMoney HTTP API - no heavy library, simple REST calls."""

    # ETF code -> EastMoney market prefix
    MARKET_MAP = {"5": "1", "6": "1", "9": "1", "0": "0", "1": "0", "3": "0", "2": "0"}

    def __init__(self):
        super().__init__("eastmoney", priority=20)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        })

    def _to_secid(self, symbol: str) -> str:
        prefix = self.MARKET_MAP.get(symbol[0], "1")
        return f"{prefix}.{symbol}"

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        try:
            secid = self._to_secid(symbol)
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": secid,
                "fields": "f43,f44,f45,f46,f47,f48,f51,f52,f57,f58,f60,f170",
                "ut": "fa5fd1943c7b386f172d6893dbbd4dc5",
            }
            resp = self.session.get(url, params=params, timeout=10)
            data = resp.json()
            if not data.get("data"):
                return None
            d = data["data"]
            now = datetime.now()
            # f43=最新价(盘中,0=未开盘) f60=昨收(*1000) f51/f52=最高/最低(*1000)
            close_val = self._safe_float(d.get("f43"))
            prev_close = (self._safe_float(d.get("f60")) or 0) / 1000.0
            if close_val is None or close_val <= 0:
                close_val = prev_close
            high_val = self._safe_float(d.get("f44"))
            low_val = self._safe_float(d.get("f45"))
            if (high_val is None or high_val <= 0):
                h = self._safe_float(d.get("f51"))
                high_val = (h / 1000.0) if h else None
            if (low_val is None or low_val <= 0):
                lv = self._safe_float(d.get("f52"))
                low_val = (lv / 1000.0) if lv else None
            open_val = self._safe_float(d.get("f46"))
            if open_val is None or open_val <= 0:
                open_val = prev_close
            change_pct = self._safe_float(d.get("f170"))
            self.record_success()
            return {
                "symbol": symbol, "name": d.get("f58", symbol),
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "open": open_val,
                "high": high_val,
                "low": low_val,
                "close": close_val,
                "volume": self._safe_float(d.get("f47")),
                "amount": self._safe_float(d.get("f48")),
                "change_pct": change_pct,
                "source": self.name,
            }
        except Exception as e:
            self.record_failure()
            logger.debug("eastmoney fetch_realtime %s: %s", symbol, e)
            return None

    def fetch_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            secid = self._to_secid(symbol)
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101", "fqt": "1",
                "beg": start_date.replace("-", ""), "end": end_date.replace("-", ""),
                "ut": "fa5fd1943c7b386f172d6893dbbd4dc5",
            }
            resp = self.session.get(url, params=params, timeout=15)
            data = resp.json()
            if not data.get("data") or not data["data"].get("klines"):
                return pd.DataFrame()

            rows = []
            for line in data["data"]["klines"]:
                parts = line.split(",")
                rows.append({
                    "date": parts[0], "open": float(parts[1]), "close": float(parts[2]),
                    "high": float(parts[3]), "low": float(parts[4]), "volume": float(parts[5]),
                    "amount": float(parts[6]), "change_pct": float(parts[8]) if len(parts) > 8 else None,
                })
            df = pd.DataFrame(rows)
            df["symbol"] = symbol
            self.record_success()
            return df[["symbol", "date", "open", "high", "low", "close", "volume", "amount", "change_pct"]]
        except Exception as e:
            self.record_failure()
            logger.debug("eastmoney fetch_history %s: %s", symbol, e)
            return pd.DataFrame()
