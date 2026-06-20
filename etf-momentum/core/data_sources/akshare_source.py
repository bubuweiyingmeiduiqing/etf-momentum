"""AKShare data source - best data quality for A-share ETFs."""

import logging
from datetime import datetime
from typing import Optional
import pandas as pd
from .base import DataSource

logger = logging.getLogger(__name__)


class AKShareSource(DataSource):
    """AKShare - richest A-share data, may have connectivity issues outside China."""

    def __init__(self):
        super().__init__("akshare", priority=30)

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        try:
            import akshare as ak
            df = ak.fund_etf_spot_em()
            row = df[df["\u4ee3\u7801"] == symbol]
            if row.empty:
                return None
            row = row.iloc[0]
            now = datetime.now()
            self.record_success()
            return {
                "symbol": symbol, "name": row.get("\u540d\u79f0", ""),
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "open": self._safe_float(row.get("\u5f00\u76d8\u4ef7")),
                "high": self._safe_float(row.get("\u6700\u9ad8\u4ef7")),
                "low": self._safe_float(row.get("\u6700\u4f4e\u4ef7")),
                "close": self._safe_float(row.get("\u6700\u65b0\u4ef7")),
                "volume": self._safe_float(row.get("\u6210\u4ea4\u91cf")),
                "amount": self._safe_float(row.get("\u6210\u4ea4\u989d")),
                "change_pct": self._safe_float(row.get("\u6da8\u8dcc\u5e45")),
                "source": self.name,
            }
        except Exception as e:
            self.record_failure()
            logger.debug("akshare fetch_realtime %s: %s", symbol, e)
            return None

    def fetch_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            import akshare as ak
            df = ak.fund_etf_hist_em(
                symbol=symbol, period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq"
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "\u65e5\u671f": "date", "\u5f00\u76d8": "open", "\u6700\u9ad8": "high",
                "\u6700\u4f4e": "low", "\u6536\u76d8": "close", "\u6210\u4ea4\u91cf": "volume",
                "\u6210\u4ea4\u989d": "amount", "\u6da8\u8dcc\u5e45": "change_pct"
            })
            df["symbol"] = symbol
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            self.record_success()
            return df[["symbol", "date", "open", "high", "low", "close", "volume", "amount", "change_pct"]]
        except Exception as e:
            self.record_failure()
            logger.debug("akshare fetch_history %s: %s", symbol, e)
            return pd.DataFrame()
