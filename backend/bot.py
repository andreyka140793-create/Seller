import os
import logging
from aiogram import Bot, Dispatcher

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Единый диспетчер на всё приложение. Все реальные хендлеры (/start, загрузка файла,
# настройка параметров, /run, /history) находятся в handlers/marginator_handler.py
# и подключаются через dp.include_router(marginator_router) в main.py (lifespan).
# Раньше здесь были свои собственные @dp.message(CommandStart()) и
# @dp.message(F.document) — они конфликтовали с более функциональным сценарием
# из marginator_handler.py (перехватывали загрузку файла раньше, чем срабатывал
# правильный FSM-обработчик) и были убраны.
dp = Dispatcher()


def get_bot_and_dp():
    if not BOT_TOKEN:
        return None, None
    bot = Bot(token=BOT_TOKEN)
    return bot, dp
