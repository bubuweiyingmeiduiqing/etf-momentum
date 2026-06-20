"""DeepSeek API client - retry, timeout, structured calls"""

import json, logging, re, time
import requests

logger = logging.getLogger(__name__)


class DeepSeekClient:

    def __init__(self, config: dict):
        ds = config.get("deepseek", {})
        self.api_key = ds.get("api_key", "")
        self.base_url = ds.get("base_url", "https://api.deepseek.com").rstrip("/")
        self.model = ds.get("model", "deepseek-chat")
        self.temperature = ds.get("temperature", 0.1)
        self.timeout = ds.get("timeout_seconds", 120)
        self.max_retries = ds.get("max_retries", 3)
        self.retry_base = ds.get("retry_base_seconds", 5)

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "stream": False,
        }
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info("DeepSeek call %d/%d", attempt, self.max_retries)
                resp = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
                if resp.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    last_error = "HTTP %d" % resp.status_code
                    wait = self.retry_base * attempt
                    logger.warning("DeepSeek temp error: %s, retry %ds", last_error, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return self._strip_fences(content)
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    time.sleep(self.retry_base * attempt)
        return '<div style="color:#b00020;">AI error: %s</div>' % last_error

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        text = re.sub(r"^```(?:html)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()
