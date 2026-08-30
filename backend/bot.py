"""Telegram bot instance."""
import os
import logging
from aiogram import Bot, Dispatcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
dp = Dispatcher()

# Текст на пустом экране чата (кнопка «Старт») — Bot API setMyDescription
BOT_DESCRIPTION = """Маржинатор считает маржу по вашему прайсу.

Загрузите Excel, CSV, PDF или фото → укажите комиссию, логистику и налог → получите отчёт: что в плюсе, что в зоне риска, Excel и мини-приложение.

Режимы: маркетплейс и B2B. Есть демо-прайс и история расчётов.

Нажмите «Старт», чтобы начать."""

# Короткое описание в профиле бота
BOT_SHORT_DESCRIPTION = "Юнит-экономика по прайсу: маржа, ROI, Excel и мини-приложение."


def get_bot_and_dp():
    if not BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return None, None
    bot = Bot(token=BOT_TOKEN)
    return bot, dp


async def setup_bot_profile(bot: Bot) -> None:
    """Показывает описание на экране с кнопкой «Старт», пока нет сообщений."""
    try:
        await bot.set_my_description(description=BOT_DESCRIPTION[:512], language_code="ru")
        await bot.set_my_description(description=BOT_DESCRIPTION[:512])  # default
    except Exception as e:
        logger.warning("set_my_description failed: %s", e)
    try:
        await bot.set_my_short_description(
            short_description=BOT_SHORT_DESCRIPTION[:120], language_code="ru"
        )
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION[:120])
    except Exception as e:
        logger.warning("set_my_short_description failed: %s", e)
