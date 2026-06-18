"""技术指标计算模块"""

import logging
import numpy as np
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class IndicatorCalculator:
    """技术指标计算器，基于行情数据计算 MA/RSI/MACD/布林带等。"""

    def __init__(self, config: dict, database=None):
        self.config = config.get("indicators", {})
        self.db = database

    def compute(self, quote: dict) -> Optional[dict]:
        """根据当前行情 + 历史数据计算一组技术指标。"""
        symbol = quote["symbol"]
        close = quote.get("close")
        if close is None:
            return None

        # 从数据库获取历史日线用于指标计算
        history = self._get_history(symbol)
        if history is None or len(history) < 2:
            return self._basic_indicators(quote)

        closes = np.array(history, dtype=float)
        return self._compute_all(closes, quote)

    def compute_from_df(self, df):
        """对 DataFrame 批量计算所有技术指标（返回新 DataFrame）。"""
        if df.empty:
            return df

        df = df.copy()
        closes = df["close"].values.astype(float)

        periods = self.config.get("ma_periods", [5, 10, 20, 60])
        for p in periods:
            df[f"ma{p}"] = self._sma(closes, p)

        df["rsi"] = self._rsi(closes, self.config.get("rsi_period", 14))

        fast = self.config.get("macd_fast", 12)
        slow = self.config.get("macd_slow", 26)
        sig = self.config.get("macd_signal", 9)
        df["macd_dif"], df["macd_dea"], df["macd_hist"] = self._macd(closes, fast, slow, sig)

        boll_p = self.config.get("boll_period", 20)
        boll_s = self.config.get("boll_std", 2)
        df["boll_upper"], df["boll_mid"], df["boll_lower"] = self._bollinger(closes, boll_p, boll_s)

        vol_ma_p = self.config.get("volume_ma_period", 20)
        volumes = df["volume"].values.astype(float) if "volume" in df.columns else np.ones_like(closes)
        df["volume_ma"] = self._sma(volumes, vol_ma_p)
        df["volume_ratio"] = np.where(df["volume_ma"] > 0, volumes / df["volume_ma"], 1.0)

        return df

    def _compute_all(self, closes: np.ndarray, quote: dict) -> dict:
        """计算所有指标并返回字典。"""
        periods = self.config.get("ma_periods", [5, 10, 20, 60])
        result = {
            "symbol": quote["symbol"],
            "timestamp": quote["timestamp"],
        }
        for p in periods:
            ma = self._sma(closes, p)
            result[f"ma{p}"] = round(ma[-1], 4) if ma[-1] is not None else None

        result["rsi"] = round(self._rsi(closes, self.config.get("rsi_period", 14))[-1], 2)

        fast = self.config.get("macd_fast", 12)
        slow = self.config.get("macd_slow", 26)
        sig = self.config.get("macd_signal", 9)
        dif, dea, hist = self._macd(closes, fast, slow, sig)
        result["macd_dif"] = round(dif[-1], 4) if dif[-1] is not None else None
        result["macd_dea"] = round(dea[-1], 4) if dea[-1] is not None else None
        result["macd_hist"] = round(hist[-1], 4) if hist[-1] is not None else None

        boll_p = self.config.get("boll_period", 20)
        boll_s = self.config.get("boll_std", 2)
        upper, mid, lower = self._bollinger(closes, boll_p, boll_s)
        result["boll_upper"] = round(upper[-1], 4) if upper[-1] is not None else None
        result["boll_mid"] = round(mid[-1], 4) if mid[-1] is not None else None
        result["boll_lower"] = round(lower[-1], 4) if lower[-1] is not None else None

        # 成交量指标
        result["volume_ma"] = quote.get("volume")  # 简化，实际需历史成交量
        result["volume_ratio"] = 1.0

        return result

    def _get_history(self, symbol: str) -> Optional[np.ndarray]:
        """从数据库获取收盘价序列。"""
        if self.db is None:
            return None
        try:
            rows = self.db.get_quotes(symbol, limit=120)
            if not rows:
                return None
            closes = [r["close"] for r in reversed(rows) if r.get("close")]
            return np.array(closes, dtype=float) if closes else None
        except Exception:
            return None

    def _basic_indicators(self, quote: dict) -> dict:
        """无历史数据时的基本指标。"""
        return {
            "symbol": quote["symbol"],
            "timestamp": quote["timestamp"],
            "ma5": quote.get("close"), "ma10": quote.get("close"),
            "ma20": quote.get("close"), "ma60": quote.get("close"),
            "rsi": 50.0,
            "macd_dif": 0, "macd_dea": 0, "macd_hist": 0,
            "boll_upper": quote.get("close"), "boll_mid": quote.get("close"),
            "boll_lower": quote.get("close"),
            "volume_ma": quote.get("volume"), "volume_ratio": 1.0,
        }

    # ---- 底层计算 ----
    @staticmethod
    def _sma(data: np.ndarray, period: int) -> np.ndarray:
        if len(data) < period:
            return np.full(len(data), None, dtype=object)
        result = np.full(len(data), None, dtype=object)
        for i in range(period - 1, len(data)):
            result[i] = np.mean(data[i - period + 1:i + 1])
        return result

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        result = np.full(len(data), None, dtype=object)
        if len(data) < period:
            return result
        k = 2 / (period + 1)
        result[period - 1] = np.mean(data[:period])
        for i in range(period, len(data)):
            result[i] = data[i] * k + result[i - 1] * (1 - k)
        return result

    @staticmethod
    def _rsi(data: np.ndarray, period: int = 14) -> np.ndarray:
        result = np.full(len(data), None, dtype=object)
        if len(data) < period + 1:
            return result
        delta = np.diff(data)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gain[:period])
        avg_loss = np.mean(loss[:period])
        for i in range(period, len(delta)):
            avg_gain = (avg_gain * (period - 1) + gain[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss[i]) / period
            if avg_loss == 0:
                result[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i + 1] = 100.0 - (100.0 / (1.0 + rs))
        return np.round(result, 2)

    @staticmethod
    def _macd(data: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = IndicatorCalculator._ema(data, fast)
        ema_slow = IndicatorCalculator._ema(data, slow)
        dif = np.where(
            (ema_fast != None) & (ema_slow != None),
            ema_fast.astype(float) - ema_slow.astype(float),
            None
        )
        valid_idx = np.where(dif != None)[0]
        if len(valid_idx) == 0:
            return dif, np.full(len(data), None), np.full(len(data), None)
        valid_dif = dif[valid_idx].astype(float)
        dea_raw = IndicatorCalculator._ema(valid_dif, signal)
        dea = np.full(len(data), None, dtype=object)
        for j, idx in enumerate(valid_idx):
            dea[idx] = dea_raw[j]
        hist = np.where(
            (dif != None) & (dea != None),
            (dif.astype(float) - dea.astype(float)) * 2,
            None
        )
        return dif, dea, hist

    @staticmethod
    def _bollinger(data: np.ndarray, period: int = 20, std_mult: int = 2):
        mid = IndicatorCalculator._sma(data, period)
        upper = np.full(len(data), None, dtype=object)
        lower = np.full(len(data), None, dtype=object)
        for i in range(period - 1, len(data)):
            std = np.std(data[i - period + 1:i + 1])
            upper[i] = mid[i] + std_mult * std
            lower[i] = mid[i] - std_mult * std
        return upper, mid, lower
