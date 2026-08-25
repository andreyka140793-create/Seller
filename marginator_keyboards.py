import os
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import models


def get_params_setup_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура быстрой настройки параметров."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Использовать по умолчанию (15% ком., 120 ₽ лог., 6% налог)",
                    callback_data="params_default"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Настроить вручную",
                    callback_data="params_custom"
                )
            ]
        ]
    )


def get_skip_keyboard() -> InlineKeyboardMarkup:
    """Кнопка пропуска для опциональных шагов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить (0 ₽ / 0%)", callback_data="skip_param")]
        ]
    )


def get_history_keyboard(uploads: list[models.PriceUpload]) -> InlineKeyboardMarkup:
    buttons = []
    for up in uploads:
        date_str = up.created_at.strftime("%d.%m %H:%M")
        btn_text = f"📄 {up.filename[:15]}... ({date_str}) | {up.total_profit:,.0f} ₽"
        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"download_upload_{up.id}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_webapp_keyboard(upload_id: int) -> InlineKeyboardMarkup:
    """Генерирует кнопку открытия Telegram Mini App для выбранной партии."""
    base_url = os.getenv("WEB_APP_URL")
    if not base_url:
        # Без WEB_APP_URL в .env кнопка будет вести на несуществующий адрес —
        # явно предупреждаем в логах вместо тихой подстановки чужого домена.
        base_url = "https://your-domain.com/app"
        print("⚠️ WEB_APP_URL не задан в переменных окружения — используется заглушка.")

    web_app_url = f"{base_url}?upload_id={upload_id}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Открыть интерактивный отчет (Mini App)",
                    web_app=WebAppInfo(url=web_app_url)
                )
            ]
        ]
    )
