import os
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    ReplyKeyboardRemove,
)
import models
from config import PurchasingConfig


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Постоянные кнопки внизу экрана — без ручного /start /run."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Новый расчёт"), KeyboardButton(text="📂 История")],
            [KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Или отправьте файл прайса…",
    )


def get_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Маркетплейс (WB / Ozon)",
                    callback_data="mode_marketplace",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏢 B2B (опт / НДС)",
                    callback_data="mode_b2b",
                )
            ],
        ]
    )


def get_params_setup_keyboard() -> InlineKeyboardMarkup:
    default_label = (
        f"⚡ По умолчанию "
        f"({PurchasingConfig.DEFAULT_MP_COMMISSION_PCT}% ком., "
        f"{PurchasingConfig.DEFAULT_LOGISTICS_RUB} ₽ лог., "
        f"{PurchasingConfig.DEFAULT_PACKAGING_RUB} ₽ уп., "
        f"{PurchasingConfig.DEFAULT_TAX_PCT}% налог)"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=default_label, callback_data="params_default")],
            [InlineKeyboardButton(text="⚙️ Настроить вручную", callback_data="params_custom")],
        ]
    )


def get_b2b_params_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ По умолчанию (фрахт 0 ₽, бонус 0%, НДС 20%)",
                    callback_data="params_default",
                )
            ],
            [InlineKeyboardButton(text="⚙️ Настроить вручную", callback_data="params_custom")],
        ]
    )


def get_run_keyboard() -> InlineKeyboardMarkup:
    """Главная кнопка вместо ввода /run."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Рассчитать", callback_data="run_calc")],
            [InlineKeyboardButton(text="⚙️ Изменить параметры", callback_data="params_custom")],
        ]
    )


def get_after_report_keyboard(webapp_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔄 Новый расчёт", callback_data="new_calc")],
    ]
    if webapp_url:
        rows.append(
            [InlineKeyboardButton(text="📊 Открыть отчёт в Mini App", web_app=WebAppInfo(url=webapp_url))]
        )
    rows.append([InlineKeyboardButton(text="📂 История", callback_data="show_history")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить (0 ₽ / 0%)", callback_data="skip_param")]
        ]
    )


def get_history_keyboard(uploads: list[models.PriceUpload]) -> InlineKeyboardMarkup:
    buttons = []
    for u in uploads[:8]:
        label = (u.filename or f"#{u.id}")[:40]
        buttons.append(
            [InlineKeyboardButton(text=f"📄 {label}", callback_data=f"hist_{u.id}")]
        )
    buttons.append([InlineKeyboardButton(text="🔄 Новый расчёт", callback_data="new_calc")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_webapp_keyboard(upload_id: int | str | None = None) -> InlineKeyboardMarkup:
    """Кнопки после расчёта: Mini App + новый расчёт."""
    base = (os.getenv("WEB_APP_URL") or "").rstrip("/")
    webapp_url = None
    if base and upload_id is not None:
        sep = "&" if "?" in base else "?"
        webapp_url = f"{base}{sep}upload_id={upload_id}"
    return get_after_report_keyboard(webapp_url)
