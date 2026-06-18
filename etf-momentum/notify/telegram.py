"""Telegram 通知模块"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """通过 Telegram Bot 发送通知消息。"""

    def __init__(self, config: dict):
        self.config = config.get("telegram", {})
        self.enabled = self.config.get("enabled", False)
        self.token = self.config.get("bot_token", "")
        self.chat_id = self.config.get("chat_id", "")
        self._bot = None

    def _get_bot(self):
        if self._bot is not None:
            return self._bot
        if not self.token or not self.chat_id:
            return None
        try:
            from telegram import Bot
            self._bot = Bot(token=self.token)
            return self._bot
        except ImportError:
            logger.warning("python-telegram-bot 未安装，Telegram 通知不可用")
            return None
        except Exception as e:
            logger.error(f"Telegram Bot 初始化失败: {e}")
            return None

    def send(self, message: str) -> bool:
        """发送文本消息。"""
        if not self.enabled:
            return False
        bot = self._get_bot()
        if bot is None:
            return False
        try:
            import asyncio
            async def _send():
                await bot.send_message(chat_id=self.chat_id, text=message[:4000])
            asyncio.get_event_loop().run_until_complete(_send())
            logger.info("Telegram 消息已发送")
            return True
        except Exception as e:
            logger.error(f"Telegram 发送失败: {e}")
            return False
