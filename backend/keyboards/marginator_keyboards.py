import os
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
)
import models
from config import PurchasingConfig


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
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
            [InlineKeyboardButton(text="🛒 Маркетплейс (WB / Ozon)", callback_data="mode_marketplace")],
            [InlineKeyboardButton(text="🏢 B2B (опт / НДС)", callback_data="mode_b2b")],
        ]
    )


def get_params_setup_keyboard() -> InlineKeyboardMarkup:
    return get_params_keyboard_with_presets([], mode="marketplace")


def get_b2b_params_keyboard() -> InlineKeyboardMarkup:
    return get_params_keyboard_with_presets([], mode="b2b")


def get_params_keyboard_with_presets(presets: list, mode: str = "marketplace") -> InlineKeyboardMarkup:
    rows = []
    if mode == "b2b":
        rows.append([InlineKeyboardButton(
            text="⚡ По умолчанию (фрахт 0, бонус 0%, НДС 20%)",
            callback_data="params_default",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text=(
                f"⚡ По умолчанию ({PurchasingConfig.DEFAULT_MP_COMMISSION_PCT}% / "
                f"{PurchasingConfig.DEFAULT_LOGISTICS_RUB}₽ / "
                f"{PurchasingConfig.DEFAULT_TAX_PCT}%)"
            ),
            callback_data="params_default",
        )])
    for pr in (presets or [])[:5]:
        rows.append([InlineKeyboardButton(
            text=f"📌 {pr.name}",
            callback_data=f"preset_{pr.id}",
        )])
    rows.append([InlineKeyboardButton(text="⚙️ Настроить вручную", callback_data="params_custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_price_col_keyboard(columns: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i, col in enumerate(columns[:10]):
        rows.append([InlineKeyboardButton(
            text=f"💰 {str(col)[:40]}",
            callback_data=f"price_col_{i}",
        )])
    rows.append([InlineKeyboardButton(text="➡️ Как определил бот", callback_data="price_col_auto")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_target_margin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="15%", callback_data="target_m_15"),
            InlineKeyboardButton(text="20%", callback_data="target_m_20"),
            InlineKeyboardButton(text="25%", callback_data="target_m_25"),
        ],
        [
            InlineKeyboardButton(text="30%", callback_data="target_m_30"),
            InlineKeyboardButton(text="40%", callback_data="target_m_40"),
        ],
        [InlineKeyboardButton(text="Пропустить", callback_data="target_m_skip")],
    ])


def get_run_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Рассчитать", callback_data="run_calc")],
        [InlineKeyboardButton(text="💾 Сохранить как пресет", callback_data="save_preset")],
        [InlineKeyboardButton(text="⚙️ Изменить параметры", callback_data="params_custom")],
    ])


def get_skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить (0 ₽ / 0%)", callback_data="skip_param")]
    ])


def get_history_keyboard(uploads: list) -> InlineKeyboardMarkup:
    buttons = []
    for u in uploads[:12]:
        name = (u.filename or f"report_{u.id}")[:28]
        profit = getattr(u, "total_profit", None)
        suffix = f" · {profit:,.0f}₽" if isinstance(profit, (int, float)) else ""
        label = f"📄 #{u.id} {name}{suffix}"[:64]
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"download_upload_{u.id}")])
    buttons.append([InlineKeyboardButton(text="🔄 Новый расчёт", callback_data="new_calc")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_after_report_keyboard(webapp_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔄 Новый расчёт", callback_data="new_calc")]]
    if webapp_url:
        rows.append([InlineKeyboardButton(
            text="📊 Открыть отчёт в Mini App",
            web_app=WebAppInfo(url=webapp_url),
        )])
    rows.append([InlineKeyboardButton(text="📂 История", callback_data="show_history")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_webapp_keyboard(upload_id: int | str | None = None) -> InlineKeyboardMarkup:
    base = (os.getenv("WEB_APP_URL") or "").strip().rstrip("/")
    webapp_url = None
    if base and upload_id is not None:
        if not base.endswith("/app") and "/app?" not in base and "/app/" not in base:
            base = base + "/app"
        sep = "&" if "?" in base else "?"
        webapp_url = f"{base}{sep}upload_id={upload_id}"
    return get_after_report_keyboard(webapp_url)
