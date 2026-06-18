"""数据采集模块 —— 使用 akshare / yfinance 获取行情数据"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class DataFetcher:
    """行情数据获取器，封装 akshare 等数据源。"""

    def __init__(self, config: dict, database=None):
        self.config = config.get("fetcher", {})
        self.symbols = self.config.get("symbols", [])
        self.db = database

    def fetch_realtime(self, symbol: str) -> Optional[dict]:
        """获取单只标的实时行情。"""
        try:
            import akshare as ak

            # A 股 ETF 实时行情
            df = ak.fund_etf_spot_em()
            row = df[df["代码"] == symbol]
            if row.empty:
                logger.warning(f"未找到标的: {symbol}")
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
            logger.error("akshare 未安装，请运行: pip install akshare")
            return self._mock_quote(symbol)
        except Exception as e:
            logger.error(f"获取 {symbol} 行情失败: {e}")
            return None

    def fetch_all_realtime(self) -> list:
        """批量获取所有监控标的的实时行情。"""
        results = []
        for sym in self.symbols:
            quote = self.fetch_realtime(sym)
            if quote:
                results.append(quote)
                if self.db:
                    self.db.insert_quote(sym, quote)
        logger.info(f"已获取 {len(results)}/{len(self.symbols)} 只标的行情")
        return results

    def fetch_history(self, symbol: str, start_date: str = None,
                      end_date: str = None) -> pd.DataFrame:
        """获取历史日线数据。"""
        if start_date is None:
            start_date = self.config.get("start_date", "2024-01-01")
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        try:
            import akshare as ak
            df = ak.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq"
            )
            if df is None or df.empty:
                logger.warning(f"{symbol} 无历史数据")
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
            logger.warning("akshare 未安装，返回空 DataFrame")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"获取 {symbol} 历史数据失败: {e}")
            return pd.DataFrame()

    def fetch_all_history(self) -> dict:
        """批量获取所有标的的历史数据。"""
        result = {}
        for sym in self.symbols:
            df = self.fetch_history(sym)
            if not df.empty:
                result[sym] = df
                # 写入日统计表
                if self.db:
                    for _, row in df.iterrows():
                        self.db.upsert_daily_summary(sym, {
                            "date": row["date"].strftime("%Y-%m-%d"),
                            "open": row.get("open"),
                            "high": row.get("high"),
                            "low": row.get("low"),
                            "close": row.get("close"),
                            "volume": row.get("volume"),
                            "change_pct": row.get("change_pct"),
                        })
        logger.info(f"已同步 {len(result)}/{len(self.symbols)} 只标的历史数据")
        return result

    def is_trade_day(self) -> bool:
        """判断当前是否为交易日（粗略：周一至周五）。"""
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
        """开发/测试用模拟数据。"""
        import random
        now = datetime.now()
        base_price = 1.0 + random.random() * 2
        change = (random.random() - 0.5) * 0.04
        return {
            "symbol": symbol,
            "name": f"ETF-{symbol}",
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(base_price, 4),
            "high": round(base_price * 1.01, 4),
            "low": round(base_price * 0.99, 4),
            "close": round(base_price + change, 4),
            "volume": random.randint(100000, 10000000),
            "amount": random.randint(1000000, 100000000),
            "change_pct": round(change / base_price * 100, 2),
        }
