"""Sina Finance HTTP API - lightweight alternative."""

import logging, re
from datetime import datetime
from typing import Optional
import pandas as pd
import requests
from .base import DataSource

logger = logging.getLogger(__name__)


class SinaSource(DataSource):
    """Sina Finance - simple HTTP API, good fallback for A-shares."""

    def __init__(self):
        super().__init__("sina", priority=40)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def _to_sina_code(self, symbol: str) -> str:
        if symbol.startswith(("5", "6", "9")):
            return f"sh{symbol}"
        return f"sz{symbol}"

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        try:
            sc = self._to_sina_code(symbol)
            url = f"https://hq.sinajs.cn/list={sc}"
            resp = self.session.get(url, timeout=10)
            resp.encoding = "gbk"
            text = resp.text
            if "FAILED" in text or "=" not in text:
                return None
            content = text.split('"')[1] if '"' in text else ""
            if not content:
                return None
            parts = content.split(",")
            if len(parts) < 32:
                return None
            now = datetime.now()
            self.record_success()
            return {
                "symbol": symbol, "name": parts[0],
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "open": self._safe_float(parts[1]),
                "close": self._safe_float(parts[3]),
                "high": self._safe_float(parts[4]),
                "low": self._safe_float(parts[5]),
                "volume": self._safe_float(parts[8]),
                "amount": self._safe_float(parts[9]),
                "change_pct": self._safe_float(parts[3]) and (
                    (self._safe_float(parts[3]) - self._safe_float(parts[2])) / self._safe_float(parts[2]) * 100
                ) if self._safe_float(parts[2]) and self._safe_float(parts[2]) != 0 else 0,
                "source": self.name,
            }
        except Exception as e:
            self.record_failure()
            logger.debug("sina fetch_realtime %s: %s", symbol, e)
            return None

    def fetch_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()
