"""Notification dispatcher - Telegram + Email + error alerts"""

import logging, traceback
from datetime import datetime

logger = logging.getLogger(__name__)


class Notifier:

    def __init__(self, config: dict):
        from notify.telegram import TelegramNotifier
        from notify.email import EmailNotifier
        self.telegram = TelegramNotifier(config)
        self.email = EmailNotifier(config)
        self.error_alerts_enabled = config.get("telegram", {}).get("error_alerts", True)

    def send_alert(self, alert: dict):
        symbol = alert.get("symbol", "")
        msg = alert.get("message", "")
        level = alert.get("level", "INFO")
        self.telegram.send_alert(symbol, level, msg)

        if level in ("WARN", "CRITICAL"):
            self.email.send(
                subject=f"[{level}] {symbol} alert",
                body=f"<h3>{level}</h3><p><b>{symbol}</b>: {msg}</p>",
                html=True,
            )

    def send_report(self, report: str):
        self.telegram.send_report("ETF Momentum Report", report)
        self.email.send(
            subject="ETF Momentum Report",
            body=report.replace("\n", "<br>"),
            html=True,
        )

    def send_error(self, component: str, error: Exception, context: str = ""):
        """Send exception alert to Telegram."""
        if not self.error_alerts_enabled:
            return
        now = datetime.now().strftime("%H:%M:%S")
        tb = "".join(traceback.format_tb(error.__traceback__))[-500:] if error.__traceback__ else ""
        text = (
            f"\u274c <b>[ERROR] {component}</b>\n"
            f"<i>{now}</i>\n"
            f"<code>{str(error)[:300]}</code>"
        )
        if context:
            text += f"\n{context[:200]}"
        if tb:
            text += f"\n<pre>{tb}</pre>"
        self.telegram.send(text)

    def send_startup(self, symbols_count: int):
        """Send startup notification."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = (
            f"\u2705 <b>ETF Momentum Started</b>\n"
            f"Time: {now}\n"
            f"Symbols: {symbols_count}\n"
            f"Mode: Production"
        )
        self.telegram.send(text)
