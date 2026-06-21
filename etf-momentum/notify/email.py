"""Email notification via SMTP (Gmail SSL port 465)."""

import logging, smtplib, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

logger = logging.getLogger(__name__)


class EmailNotifier:

    def __init__(self, config: dict):
        ec = config.get("email", {})
        self.enabled = ec.get("enabled", False)
        self.smtp_host = ec.get("smtp_host", "smtp.gmail.com")
        self.smtp_port = int(ec.get("smtp_port", 465))
        self.sender = ec.get("sender", "")
        self.password = ec.get("password", "")
        # Support both "receiver" (comma-sep string) and "recipients" (list)
        receiver = ec.get("receiver", "")
        if receiver and isinstance(receiver, str):
            self.recipients = [r.strip() for r in receiver.split(",") if r.strip()]
        else:
            self.recipients = ec.get("recipients", [])
        self.max_retries = ec.get("max_retries", 2)

        # Log config state on init
        if self.enabled:
            if not self.sender:
                logger.warning("Email enabled but sender not set")
            if not self.password:
                logger.warning("Email enabled but password not set")
            if not self.recipients:
                logger.warning("Email enabled but no recipients configured")
        else:
            logger.info("Email notifier DISABLED in config")

    def send(self, subject: str, body: str, html: bool = True) -> bool:
        if not self.enabled:
            logger.debug("Email disabled, skipping send")
            return False
        if not self.sender or not self.password or not self.recipients:
            logger.warning("Email config incomplete (sender=%s, password_set=%s, recipients=%d)",
                           bool(self.sender), bool(self.password), len(self.recipients))
            return False

        for attempt in range(1, self.max_retries + 1):
            try:
                msg = MIMEMultipart()
                msg["From"] = self.sender
                msg["To"] = ", ".join(self.recipients)
                msg["Subject"] = Header(subject, "utf-8")
                content_type = "html" if html else "plain"
                msg.attach(MIMEText(body, content_type, "utf-8"))

                # Gmail uses SMTP_SSL on port 465
                if self.smtp_port == 465:
                    with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30) as server:
                        server.login(self.sender, self.password)
                        server.sendmail(self.sender, self.recipients, msg.as_string())
                else:
                    with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                        server.starttls()
                        server.login(self.sender, self.password)
                        server.sendmail(self.sender, self.recipients, msg.as_string())

                logger.info("Email sent to %d recipients", len(self.recipients))
                return True
            except smtplib.SMTPAuthenticationError as e:
                logger.error("Email AUTH FAILED: check Gmail App Password (not account password). Error: %s", e)
                return False
            except Exception as e:
                if attempt < self.max_retries:
                    logger.warning("Email attempt %d/%d failed: %s, retrying...", attempt, self.max_retries, e)
                    time.sleep(3)
                else:
                    logger.error("Email send exhausted after %d attempts: %s", self.max_retries, e)
                    return False
        return False
