"""邮件通知模块"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


class EmailNotifier:
    """通过 SMTP 发送邮件通知。"""

    def __init__(self, config: dict):
        self.config = config.get("email", {})
        self.enabled = self.config.get("enabled", False)
        self.smtp_host = self.config.get("smtp_host", "smtp.gmail.com")
        self.smtp_port = self.config.get("smtp_port", 587)
        self.sender = self.config.get("sender", "")
        self.password = self.config.get("password", "")
        self.recipients = self.config.get("recipients", [])

    def send(self, subject: str, body: str, html: bool = False) -> bool:
        """发送邮件。"""
        if not self.enabled:
            return False
        if not self.sender or not self.password or not self.recipients:
            logger.warning("邮件配置不完整，跳过发送")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.sender
            msg["To"] = ", ".join(self.recipients)
            msg["Subject"] = subject
            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type, "utf-8"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipients, msg.as_string())

            logger.info(f"邮件已发送至 {len(self.recipients)} 位收件人")
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False
