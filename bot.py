import os
import logging
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile

from config import PurchasingConfig

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# URL нашего бэкенда (на локальном сервере 127.0.0.1:8000, на Amvera 127.0.0.1:80)
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/upload-price/")

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 **Привет! Я ИИ-агент по закупкам и перепродаже.**\n\n"
        "📊 Отправь мне прайс-лист поставщика в формате `.xlsx` или `.csv`.\n"
        "Я автоматически распознаю структуру таблицы, рассчитаю юнит-экономику "
        "(комиссии, налоги, логистику) и пришлю тебе список самых маржинальных товаров!"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message(F.document)
async def handle_document(message: types.Message, bot: Bot):
    document = message.document
    filename = document.file_name

    if not (filename.endswith('.xlsx') or filename.endswith('.xls') or filename.endswith('.csv')):
        await message.answer("⚠️ Пожалуйста, отправьте файл в формате Excel (.xlsx) или CSV (.csv).")
        return

    status_msg = await message.answer("⏳ **Файл получен.** ИИ-агент анализирует прайс-лист...")

    try:
        # Загружаем файл из Telegram
        file_info = await bot.get_file(document.file_id)
        file_bytes = await bot.download_file(file_info.file_path)

        # Формируем multipart-запрос к нашему FastAPI бэкенду
        data = aiohttp.FormData()
        data.add_field("telegram_id", str(message.from_user.id))
        data.add_field("file", file_bytes.read(), filename=filename, content_type=document.mime_type or "application/octet-stream")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.post(API_URL, data=data) as response:
                if response.status != 200:
                    err_text = await response.text()
                    await status_msg.edit_text(f"❌ Ошибка при обработке файла: {err_text}")
                    return

                res_json = await response.json()

        total = res_json.get("total_items_processed", 0)
        profitable_count = res_json.get("profitable_items_count", 0)
        top_items = res_json.get("top_profitable_items", [])

        if profitable_count == 0:
            await status_msg.edit_text(
                f"✅ **Обработка завершена.** Проверено товаров: {total}.\n"
                f"😔 К сожалению, товаров с ROI ≥ {PurchasingConfig.MIN_ROI_PCT:.0f}% не найдено."
            )
            return

        report = [
            f"🎉 **Найдено маржинальных товаров: {profitable_count} из {total}**\n",
            f"🔥 **ТОП выгодных позиций (ROI ≥ {PurchasingConfig.MIN_ROI_PCT:.0f}%):**\n"
        ]

        for idx, item in enumerate(top_items, 1):
            report.append(
                f"{idx}. **{item['title']}**\n"
                f"   • Закупка: `{item['buy_price']} ₽` ➔ Продажа: `{item['est_sell_price']} ₽`\n"
                f"   • Чистая прибыль: **+{item['net_profit']} ₽** | ROI: **{item['roi_pct']}%**\n"
            )

        await status_msg.edit_text("\n".join(report), parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")

def get_bot_and_dp():
    if not BOT_TOKEN:
        return None, None
    bot = Bot(token=BOT_TOKEN)
    return bot, dp
