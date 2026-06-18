"""通知模块 - 统一通知分发器"""

import logging

logger = logging.getLogger(__name__)


class Notifier:
    """统一通知分发器，整合 Telegram + Email + 日志输出。"""

    def __init__(self, config: dict):
        from notify.telegram import TelegramNotifier
        from notify.email import EmailNotifier

        self.telegram = TelegramNotifier(config)
        self.email = EmailNotifier(config)

    def send_alert(self, alert: dict) -> None:
        """发送告警通知（多渠道）。"""
        symbol = alert.get("symbol", "")
        msg = alert.get("message", "")
        level = alert.get("level", "INFO")

        # Telegram 通知
        emoji = {"INFO": "ℹ️", "WARN": "⚠️", "CRITICAL": "🚨"}.get(level, "ℹ️")
        tg_msg = f"{emoji} [{level}] {symbol}\n{msg}"
        self.telegram.send(tg_msg)

        # 邮件通知（仅 WARN 和 CRITICAL 级别）
        if level in ("WARN", "CRITICAL"):
            self.email.send(
                subject=f"[{level}] {symbol} 告警",
                body=f"<h3>{level} 告警</h3><p><b>{symbol}</b>: {msg}</p>",
                html=True,
            )

    def send_report(self, report: str) -> None:
        """发送汇总报告。"""
        self.telegram.send(report)
        self.email.send(
            subject="📊 ETF Momentum 日终报告",
            body=report.replace("\n", "<br>"),
            html=True,
        )
