"""告警规则引擎 —— 检测价格、量能、指标异常并生成告警"""

import logging
from datetime import datetime
from typing import Optional

from core.models import Alert, Signal

logger = logging.getLogger(__name__)


class Alerter:
    """告警检查器，基于规则引擎检测异常情况。"""

    def __init__(self, config: dict, database=None):
        self.config = config.get("alerts", {})
        self.db = database

    def check(self, quote: dict, indicators: Optional[dict] = None) -> list:
        """检查所有告警规则，返回触发的告警列表。"""
        alerts = []
        alerts.extend(self._check_price_change(quote))
        alerts.extend(self._check_volume(quote, indicators))
        alerts.extend(self._check_rsi(quote, indicators))
        alerts.extend(self._check_ma_cross(quote, indicators))
        alerts.extend(self._check_bollinger(quote, indicators))
        return alerts

    def _check_price_change(self, quote: dict) -> list:
        """涨跌幅告警。"""
        threshold = self.config.get("price_change_pct", 3.0)
        change_pct = quote.get("change_pct")
        if change_pct is None:
            return []
        alerts = []
        if abs(change_pct) >= threshold:
            direction = "上涨" if change_pct > 0 else "下跌"
            alerts.append(Alert(
                symbol=quote["symbol"],
                name=quote.get("name"),
                alert_type="PRICE_CHANGE",
                level="WARN" if abs(change_pct) >= threshold * 1.5 else "INFO",
                message=f"{quote['symbol']} {direction} {abs(change_pct):.2f}%，当前价 {quote.get('close')}",
                price=quote.get("close"),
                timestamp=datetime.now(),
            ).to_dict())
        return alerts

    def _check_volume(self, quote: dict, indicators: Optional[dict]) -> list:
        """成交量异常告警。"""
        ratio_threshold = self.config.get("volume_ratio", 2.0)
        alerts = []
        if indicators and indicators.get("volume_ratio", 1.0) >= ratio_threshold:
            alerts.append(Alert(
                symbol=quote["symbol"],
                name=quote.get("name"),
                alert_type="VOLUME_SPIKE",
                level="INFO",
                message=f"{quote['symbol']} 成交量放大 {indicators['volume_ratio']:.1f} 倍",
                price=quote.get("close"),
                timestamp=datetime.now(),
            ).to_dict())
        return alerts

    def _check_rsi(self, quote: dict, indicators: Optional[dict]) -> list:
        """RSI 超买超卖告警。"""
        alerts = []
        if indicators is None or indicators.get("rsi") is None:
            return alerts
        rsi = indicators["rsi"]
        overbought = self.config.get("rsi_overbought", 70)
        oversold = self.config.get("rsi_oversold", 30)
        if rsi >= overbought:
            alerts.append(Alert(
                symbol=quote["symbol"], name=quote.get("name"),
                alert_type="RSI_OVERBOUGHT", level="WARN",
                message=f"{quote['symbol']} RSI={rsi} 进入超买区 (>={overbought})",
                price=quote.get("close"), timestamp=datetime.now(),
            ).to_dict())
        elif rsi <= oversold:
            alerts.append(Alert(
                symbol=quote["symbol"], name=quote.get("name"),
                alert_type="RSI_OVERSOLD", level="INFO",
                message=f"{quote['symbol']} RSI={rsi} 进入超卖区 (<={oversold})",
                price=quote.get("close"), timestamp=datetime.now(),
            ).to_dict())
        return alerts

    def _check_ma_cross(self, quote: dict, indicators: Optional[dict]) -> list:
        """均线交叉告警（简化版：仅基于当前快照）。"""
        return []  # 完整实现需对比上一次指标数据

    def _check_bollinger(self, quote: dict, indicators: Optional[dict]) -> list:
        """布林带突破告警。"""
        alerts = []
        if not self.config.get("boll_breakout", True):
            return alerts
        if indicators is None:
            return alerts
        close = quote.get("close")
        if close and indicators.get("boll_upper") and close >= indicators["boll_upper"]:
            alerts.append(Alert(
                symbol=quote["symbol"], name=quote.get("name"),
                alert_type="BOLL_BREAKOUT_UP", level="INFO",
                message=f"{quote['symbol']} 突破布林带上轨 {indicators['boll_upper']}",
                price=close, timestamp=datetime.now(),
            ).to_dict())
        elif close and indicators.get("boll_lower") and close <= indicators["boll_lower"]:
            alerts.append(Alert(
                symbol=quote["symbol"], name=quote.get("name"),
                alert_type="BOLL_BREAKOUT_DOWN", level="INFO",
                message=f"{quote['symbol']} 跌破布林带下轨 {indicators['boll_lower']}",
                price=close, timestamp=datetime.now(),
            ).to_dict())
        return alerts

    def generate_signal(self, symbol: str, indicators: dict, quote: dict) -> Optional[Signal]:
        """根据指标生成交易信号（示例：RSI + MACD 综合判断）。"""
        rsi = indicators.get("rsi")
        macd_hist = indicators.get("macd_hist")
        if rsi is None or macd_hist is None:
            return None

        signal = None
        if rsi < 30 and macd_hist > 0:
            signal = Signal(
                symbol=symbol, name=quote.get("name"),
                signal_type="BUY", direction="LONG",
                price=quote.get("close"), reason="RSI超卖 + MACD金叉",
                strength=0.7,
            )
        elif rsi > 70 and macd_hist < 0:
            signal = Signal(
                symbol=symbol, name=quote.get("name"),
                signal_type="SELL", direction="SHORT",
                price=quote.get("close"), reason="RSI超买 + MACD死叉",
                strength=0.6,
            )

        if signal and self.db:
            self.db.insert_signal(signal.to_dict())
        return signal
