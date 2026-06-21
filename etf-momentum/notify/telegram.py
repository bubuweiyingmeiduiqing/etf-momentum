"""Telegram notification via HTTP Bot API (no async dependency)"""

import logging, json, time, re
import requests

logger = logging.getLogger(__name__)

# Telegram HTML parse mode only supports these tags
TELEGRAM_ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler"}


def strip_unsupported_html(html_text: str) -> str:
    """Remove HTML tags that Telegram parse mode does not support.
    Converts <h1>-<h6> to <b>, <li> to bullet prefix, strips <p>/<div>/<span>/<ul>/<ol>/<table>/<section>/<hr>/<br/>.
    """
    # Replace heading tags with bold
    for tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        html_text = re.sub(rf"<{tag}[^>]*>", "<b>", html_text, flags=re.IGNORECASE)
        html_text = re.sub(rf"</{tag}>", "</b>\n", html_text, flags=re.IGNORECASE)

    # Replace <li> with "• " prefix, strip </li>
    html_text = re.sub(r"<li[^>]*>", "• ", html_text, flags=re.IGNORECASE)
    html_text = re.sub(r"</li>", "\n", html_text, flags=re.IGNORECASE)

    # Replace <br/> and <br> with newline
    html_text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)

    # Replace </ul> and </ol> with double newline
    html_text = re.sub(r"</(ul|ol)>", "\n", html_text, flags=re.IGNORECASE)

    # Replace </p> with double newline
    html_text = re.sub(r"</p>", "\n\n", html_text, flags=re.IGNORECASE)

    # Strip all remaining HTML tags that Telegram doesn't support
    html_text = re.sub(r"</?(?:div|span|section|article|header|footer|nav|aside|main|table|thead|tbody|tr|td|th|hr|ol|ul|p|br|img|svg|style|script|meta|link|head|body|html|form|input|button|select|option|label|fieldset|legend|iframe|object|embed|video|audio|source|canvas|figure|figcaption|blockquote|details|summary|dialog|menu|menuitem|wbr|noscript|map|area|col|colgroup|caption)[^>]*>", "", html_text, flags=re.IGNORECASE)

    # Collapse multiple consecutive newlines
    html_text = re.sub(r"\n{4,}", "\n\n\n", html_text)

    # Strip leading/trailing whitespace
    html_text = html_text.strip()

    return html_text


class TelegramNotifier:

    def __init__(self, config: dict):
        tg = config.get("telegram", {})
        self.enabled = tg.get("enabled", False)
        self.token = tg.get("bot_token", "")
        self.chat_id = str(tg.get("chat_id", ""))
        self.max_retries = tg.get("max_retries", 3)
        self.api_base = f"https://api.telegram.org/bot{self.token}" if self.token else ""

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled or not self.token or not self.chat_id:
            return False
        # Sanitize HTML for Telegram
        safe_message = strip_unsupported_html(message) if parse_mode == "HTML" else message
        # Truncate to Telegram limit
        safe_message = safe_message[:4096]
        # If after sanitization the message is empty or just bold tags, skip
        clean = re.sub(r"</?b>", "", safe_message).strip()
        if not clean:
            return True  # nothing meaningful to send
        url = f"{self.api_base}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": safe_message,
            "parse_mode": parse_mode,
        }
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=15)
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    return True
                err = data.get("description", f"HTTP {resp.status_code}")
                if resp.status_code == 429 and attempt < self.max_retries:
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning("Telegram rate limited, retry after %ds", retry_after)
                    time.sleep(retry_after)
                    continue
                logger.error("Telegram send failed: %s", err)
                return False
            except Exception as e:
                if attempt < self.max_retries:
                    logger.warning("Telegram attempt %d/%d failed: %s", attempt, self.max_retries, e)
                    time.sleep(2 * attempt)
                else:
                    logger.error("Telegram send exhausted: %s", e)
                    return False
        return False

    def send_alert(self, symbol: str, level: str, message: str) -> bool:
        emoji = {"INFO": "\u2139\ufe0f", "WARN": "\u26a0\ufe0f", "CRITICAL": "\ud83d\udea8"}.get(level, "\u2139\ufe0f")
        text = f"{emoji} <b>[{level}] {symbol}</b>\n{message}"
        return self.send(text)

    def send_report(self, title: str, content: str) -> bool:
        text = f"<b>{title}</b>\n\n{content}"
        return self.send(text)

    def send_long(self, title: str, content: str, parse_mode: str = "HTML") -> int:
        """Send long message split into Telegram-safe chunks (<4096 chars). Returns chunk count."""
        if not self.enabled or not self.token or not self.chat_id:
            return 0
        header = f"<b>{title}</b>\n\n"
        header_len = len(header)
        paragraphs = content.split("\n\n")
        chunks = []
        current = header
        for para in paragraphs:
            candidate = current + ("\n\n" if current != header else "") + para
            if len(candidate) > 4000:
                if current != header:
                    chunks.append(current)
                current = header + para if para else header
            else:
                current = candidate
        if current != header:
            chunks.append(current)
        if not chunks and content:
            chunks = [header + content[:3900] + "\n\n<i>(truncated)</i>"]
        for i, chunk in enumerate(chunks):
            suffix = f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
            ok = self.send(chunk[:4096] + suffix, parse_mode)
            if not ok:
                logger.error("Telegram chunk %d/%d failed", i+1, len(chunks))
        return len(chunks)

    def get_chat_id(self) -> str:
        """Fetch the latest chat_id from updates (run once after user sends /start to bot)."""
        if not self.token:
            return ""
        try:
            resp = requests.get(f"{self.api_base}/getUpdates", timeout=10)
            data = resp.json()
            if data.get("ok") and data.get("result"):
                chat_id = str(data["result"][-1]["message"]["chat"]["id"])
                logger.info("Detected chat_id: %s", chat_id)
                return chat_id
        except Exception as e:
            logger.error("getUpdates failed: %s", e)
        return ""
