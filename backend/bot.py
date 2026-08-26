"""Telegram bot instance."""
import os
import logging
from aiogram import Bot, Dispatcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
dp = Dispatcher()


def get_bot_and_dp():
    if not BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return None, None
    bot = Bot(token=BOT_TOKEN)
    return bot, dp
