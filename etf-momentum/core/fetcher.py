"""数据采集模块 — 支持指数退避重试、熔断保护、多数据源降级"""

import logging
import random
from datetime import datetime
from typing import Optional

import pandas as pd

from utils.retry import retry_on_failure, CircuitBreaker
from utils.health import HealthChecker

logger = logging.getLogger(__name__)

# 全局熔断器：连续失败5次后熔断60秒
_fetcher_breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=60.0)


class DataFetcher:
    """行情数据获取器，支持 akshare / yfinance 双数据源自动降级。"""

    def __init__(self, config: dict, database=None):
        fc = config.get("fetcher", {})
        self.config = fc
        self.symbols = fc.get("symbols", [])
        self.db = database
        self.max_retries = fc.get("max_retries", 3)
        self.retry_base_delay = fc.get("retry_base_delay", 2.0)
        self.use_mock_fallback = fc.get("use_mock_fallback", False)

    @retry_on_failure(max_retries=3, base_delay=2.0, max_delay=30.0,
                      circuit_breaker=_fetcher_breaker)
    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        """获取单只标的实时行情（含重试+熔断）。"""
        try:
            import akshare as ak
            df = ak.fund_etf_spot_em()
            row = df[df["代码"] == symbol]
            if row.empty:
                logger.warning("未找到标的: %s", symbol)
                return None

            row = row.iloc[0]
            now = datetime.now()
            return {
                "symbol": symbol,
                "name": row.get("名称", ""),
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "open": self._safe_float(row.get("开盘价")),
                "high": self._safe_float(row.get("最高价")),
                "low": self._safe_float(row.get("最低价")),
                "close": self._safe_float(row.get("最新价")),
                "volume": self._safe_float(row.get("成交量")),
                "amount": self._safe_float(row.get("成交额")),
                "change_pct": self._safe_float(row.get("涨跌幅")),
            }
        except ImportError:
            logger.warning("akshare 未安装")
            return self._fallback_quote(symbol)
        except Exception as e:
            logger.warning("akshare 获取 %s 失败: %s，尝试 yfinance 降级", symbol, e)
            return self._fetch_via_yfinance(symbol)

    def _fetch_via_yfinance(self, symbol: str) -> Optional[dict]:
        """yfinance 降级数据源。"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.history(period="1d")
            if info.empty:
                return self._fallback_quote(symbol)
            row = info.iloc[-1]
            now = datetime.now()
            prev_close = info["Close"].iloc[-2] if len(info) >= 2 else row["Close"]
            change_pct = ((row["Close"] - prev_close) / prev_close * 100) if prev_close else 0
            return {
                "symbol": symbol,
                "name": symbol,
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
                "amount": None,
                "change_pct": round(change_pct, 2),
            }
        except Exception as e:
            logger.warning("yfinance 降级也失败: %s", e)
            return self._fallback_quote(symbol)

    def _fallback_quote(self, symbol: str) -> Optional[dict]:
        """最终降级：mock 数据 或 None。"""
        if self.use_mock_fallback:
            logger.warning("使用模拟数据: %s", symbol)
            return self._mock_quote(symbol)
        logger.error("所有数据源均失败，放弃获取: %s", symbol)
        return None

    def fetch_all_realtime(self) -> list:
        """批量获取所有监控标的的实时行情。"""
        results = []
        failed = []
        for sym in self.symbols:
            try:
                quote = self.fetch_realtime(sym)
                if quote:
                    results.append(quote)
                    if self.db:
                        self.db.insert_quote(sym, quote)
                else:
                    failed.append(sym)
            except Exception as e:
                logger.error("获取 %s 失败（重试耗尽）: %s", sym, e)
                failed.append(sym)
        if failed:
            logger.warning("以下标的获取失败: %s", ", ".join(failed))
        logger.info("已获取 %d/%d 只标的行情", len(results), len(self.symbols))
        return results

    def fetch_history(self, symbol: str, start_date: str = None,
                      end_date: str = None) -> pd.DataFrame:
        """获取历史日线数据（含重试）。"""
        if start_date is None:
            start_date = self.config.get("start_date", "2024-01-01")
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        for attempt in range(1, 4):
            try:
                import akshare as ak
                df = ak.fund_etf_hist_em(
                    symbol=symbol, period="daily",
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                    adjust="qfq"
                )
                if df is None or df.empty:
                    logger.warning("%s 无历史数据", symbol)
                    return pd.DataFrame()

                df = df.rename(columns={
                    "日期": "date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume",
                    "成交额": "amount", "涨跌幅": "change_pct"
                })
                df["symbol"] = symbol
                df["date"] = pd.to_datetime(df["date"])
                return df[["symbol", "date", "open", "high", "low", "close",
                           "volume", "amount", "change_pct"]]
            except ImportError:
                logger.warning("akshare 未安装")
                return pd.DataFrame()
            except Exception as e:
                if attempt == 3:
                    logger.error("获取 %s 历史数据失败（重试3次）: %s", symbol, e)
                    return pd.DataFrame()
                logger.warning("获取 %s 历史数据第 %d/3 次失败: %s，重试中...", symbol, attempt, e)
                import time
                time.sleep(2 ** attempt)

        return pd.DataFrame()

    def fetch_all_history(self) -> dict:
        """批量获取所有标的的历史数据。"""
        result = {}
        for sym in self.symbols:
            df = self.fetch_history(sym)
            if not df.empty:
                result[sym] = df
                if self.db:
                    for _, row in df.iterrows():
                        self.db.upsert_daily_summary(sym, {
                            "date": row["date"].strftime("%Y-%m-%d"),
                            "open": row.get("open"), "high": row.get("high"),
                            "low": row.get("low"), "close": row.get("close"),
                            "volume": row.get("volume"),
                            "change_pct": row.get("change_pct"),
                        })
        logger.info("已同步 %d/%d 只标的历史数据", len(result), len(self.symbols))
        return result

    def is_trade_day(self) -> bool:
        now = datetime.now()
        return now.weekday() < 5

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None or val == "-" or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _mock_quote(symbol: str) -> dict:
        import random
        now = datetime.now()
        base_price = 1.0 + random.random() * 2
        change = (random.random() - 0.5) * 0.04
        return {
            "symbol": symbol, "name": f"ETF-{symbol}",
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(base_price, 4),
            "high": round(base_price * 1.01, 4),
            "low": round(base_price * 0.99, 4),
            "close": round(base_price + change, 4),
            "volume": random.randint(100000, 10000000),
            "amount": random.randint(1000000, 100000000),
            "change_pct": round(change / base_price * 100, 2),
        }
