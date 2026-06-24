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

    def send_daily_report(self, trade_date: str, html_content: str):
        """Send daily report via Telegram (full content) + Email (full HTML)."""
        # Telegram: full report content, chunked if >4096 chars
        title = f"📊 ETF Daily Report {trade_date}"
        chunks = self.telegram.send_long(title, html_content)
        if chunks:
            logger.info("Telegram report sent: %d chunk(s)", chunks)
        else:
            logger.warning("Telegram report NOT sent (disabled or unconfigured)")

        # Email: full HTML report
        if self.email.enabled:
            subject = f"📊 ETF Daily Report {trade_date}"
            body = f"""<html>
<body style="font-family:Arial,'Microsoft YaHei',sans-serif;line-height:1.65;color:#222;">
  <h1 style="color:#0b5394;border-bottom:2px solid #0b5394;padding-bottom:8px;">ETF Momentum 量化日报</h1>
  <p style="color:#666;">{trade_date}</p>
  <section>{html_content}</section>
  <hr style="margin:24px 0;border:0;border-top:1px solid #ddd;">
  <p style="font-size:12px;color:#888;">ETF Momentum System · Auto-generated</p>
</body>
</html>"""
            ok = self.email.send(subject=subject, body=body, html=True)
            if ok:
                logger.info("Email report sent to %d recipients", len(self.email.recipients))
            else:
                logger.error("Email report FAILED for %s", trade_date)
        else:
            logger.warning("Email disabled in config, skipping email report for %s", trade_date)

    def send_error(self, component: str, error: Exception, context: str = ""):
        """Send exception alert to Telegram."""
        if not self.error_alerts_enabled:
            return
        now = datetime.now().strftime("%H:%M:%S")
        tb = "".join(traceback.format_tb(error.__traceback__))[-500:] if error.__traceback__ else ""
        text = (
            f"❌ <b>[ERROR] {component}</b>\n"
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
            f"✅ <b>ETF Momentum Started</b>\n"
            f"Time: {now}\n"
            f"Symbols: {symbols_count}\n"
            f"Mode: Production"
        )
        self.telegram.send(text)
    def send_review_report(self, review_type: str, start_date: str, end_date: str, html_content: str):
        """Send review report via Telegram + Email with different title from daily."""
        type_label = "Weekly" if review_type == "weekly" else "Monthly"
        emoji = "U0001F4CB" if review_type == "weekly" else "U0001F4C5"
        title = f"{emoji} ETF {type_label} Review {start_date} ~ {end_date}"

        self.telegram.send_long(title, html_content)

        if self.email.enabled:
            subject = f"{emoji} ETF {type_label} Review {start_date} ~ {end_date}"
            body = f"""<html>
<body style="font-family:Arial,'Microsoft YaHei',sans-serif;line-height:1.65;color:#222;">
  <h1 style="color:#0b5394;border-bottom:2px solid #0b5394;padding-bottom:8px;">ETF Momentum {type_label} Review</h1>
  <p style="color:#666;">{start_date} ~ {end_date}</p>
  <section>{html_content}</section>
  <hr style="margin:24px 0;border:0;border-top:1px solid #ddd;">
  <p style="font-size:12px;color:#888;">ETF Momentum System · Auto-generated · CSO Review</p>
</body>
</html>"""
            ok = self.email.send(subject=subject, body=body, html=True)
            if ok:
                logger.info("%s review email sent", review_type)
            else:
                logger.error("%s review email FAILED", review_type)

    def send_rebalance_reminder(self, trade_date: str, holdings: list, candidates: list):
        """Send rebalance reminder email before Monday rebalance day."""
        if not self.email.enabled:
            logger.warning("Email disabled, skipping rebalance reminder")
            return

        hold_rows = ""
        if holdings:
            for h in holdings:
                hold_rows += f"<tr><td>{h.get('code','')}</td><td>{h.get('name','')}</td><td>{h.get('pct',0)}%</td><td>{h.get('score',0):.2f}</td></tr>\n"
        else:
            hold_rows = "<tr><td colspan='4'>No current positions</td></tr>"

        cand_rows = ""
        if candidates:
            for i, c in enumerate(candidates):
                cand_rows += f"<tr><td>{i+1}</td><td>{c[0]}</td><td>{c[1]}</td><td>{c[2]:.2f}</td><td>{c[3]:.2f}%</td></tr>\n"
        else:
            cand_rows = "<tr><td colspan='5'>No candidates passed filter</td></tr>"

        body = f"""<html>
<body style="font-family:Arial,'Microsoft YaHei',sans-serif;line-height:1.65;color:#222;">
  <h1 style="color:#e67e00;border-bottom:2px solid #e67e00;padding-bottom:8px;">U0001F504 ETF Rebalance Reminder</h1>
  <p style="font-size:16px;color:#333;"><b>Upcoming Rebalance: {trade_date} (Monday)</b></p>
  <p style="color:#666;">This is your pre-rebalance reminder. Review the data below before making adjustments.</p>

  <h2 style="color:#0b5394;">Current Positions</h2>
  <table border=1 cellpadding=6 cellspacing=0 style="border-collapse:collapse;width:100%">
    <tr style="background:#0b5394;color:#fff"><th>Code</th><th>Name</th><th>Weight</th><th>Score</th></tr>
    {hold_rows}
  </table>

  <h2 style="color:#0b5394;">Top Candidates (filter passed)</h2>
  <table border=1 cellpadding=6 cellspacing=0 style="border-collapse:collapse;width:100%">
    <tr style="background:#0b5394;color:#fff"><th>Rank</th><th>Code</th><th>Name</th><th>Score</th><th>ATR%</th></tr>
    {cand_rows}
  </table>

  <p style="margin-top:20px;color:#e67e00;"><b>Action Required:</b> Review and execute rebalance orders before market close on {trade_date}.</p>
  <hr style="margin:24px 0;border:0;border-top:1px solid #ddd;">
  <p style="font-size:12px;color:#888;">ETF Momentum System · Auto-generated · Rebalance Reminder</p>
</body>
</html>"""

        subject = f"U0001F504 ETF Rebalance Reminder - {trade_date}"
        ok = self.email.send(subject=subject, body=body, html=True)
        if ok:
            tg_msg = f"U0001F504 <b>Rebalance Reminder</b>\nDate: {trade_date}\nCheck email for full details."
            self.telegram.send(tg_msg)
            logger.info("Rebalance reminder sent for %s", trade_date)
        else:
            logger.error("Rebalance reminder FAILED for %s", trade_date)

