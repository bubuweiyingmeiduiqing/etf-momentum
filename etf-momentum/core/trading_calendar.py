"""A股交易日历 —— 基于 akshare 获取真实交易日"""

import logging
from datetime import datetime, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)


class TradingCalendar:
    """A股交易日历，缓存交易日列表。"""

    def __init__(self):
        self._trade_days_cache = None
        self._cache_year = None

    def _fetch_trade_days(self, year: int = None):
        """从 akshare 获取全年交易日。"""
        try:
            import akshare as ak
            if year is None:
                year = datetime.now().year
            df = ak.tool_trade_date_hist_sina()
            days = set()
            for _, row in df.iterrows():
                d = str(row["trade_date"])
                if d.startswith(str(year)):
                    days.add(d)
            logger.info("已加载 %d 年交易日: %d 天", year, len(days))
            return days
        except Exception as e:
            logger.warning("获取交易日历失败: %s，回退到周一至周五", e)
            return None

    def get_trade_days(self) -> set:
        """获取当前年份交易日集合，格式 'YYYY-MM-DD'。"""
        year = datetime.now().year
        if self._trade_days_cache is None or self._cache_year != year:
            self._trade_days_cache = self._fetch_trade_days(year)
            self._cache_year = year
        return self._trade_days_cache or set()

    def is_trade_day(self, date=None) -> bool:
        """判断是否为A股交易日。"""
        if date is None:
            date = datetime.now()
        if isinstance(date, datetime):
            date_str = date.strftime("%Y-%m-%d")
        else:
            date_str = str(date)
        trade_days = self.get_trade_days()
        if trade_days:
            return date_str in trade_days
        # 回退：周一至周五
        if isinstance(date, str):
            date = datetime.strptime(date, "%Y-%m-%d")
        return date.weekday() < 5

    def next_trade_day(self, from_date=None) -> str:
        """获取下一个交易日。"""
        if from_date is None:
            from_date = datetime.now()
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, "%Y-%m-%d")
        current = from_date + timedelta(days=1)
        for _ in range(30):  # 最多找30天
            if self.is_trade_day(current):
                return current.strftime("%Y-%m-%d")
            current += timedelta(days=1)
        return (from_date + timedelta(days=1)).strftime("%Y-%m-%d")

    def last_trade_day(self, from_date=None) -> str:
        """获取最近一个交易日（含今天）。"""
        if from_date is None:
            from_date = datetime.now()
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, "%Y-%m-%d")
        current = from_date
        for _ in range(30):
            if self.is_trade_day(current):
                return current.strftime("%Y-%m-%d")
            current -= timedelta(days=1)
        return from_date.strftime("%Y-%m-%d")

    def previous_trade_day(self, from_date=None) -> str:
        """获取上一个交易日（不含今天）。"""
        if from_date is None:
            from_date = datetime.now()
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, "%Y-%m-%d")
        return self.last_trade_day(from_date - timedelta(days=1))
