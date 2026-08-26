import os
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
)
from config import PurchasingConfig


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Новый расчёт"), KeyboardButton(text="📂 История")],
            [KeyboardButton(text="📖 Помощь"), KeyboardButton(text="❌ Отмена")],
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
            [InlineKeyboardButton(text="⚖️ Сравнить 2 прайса", callback_data="mode_compare")],
        ]
    )


def get_params_setup_keyboard() -> InlineKeyboardMarkup:
    return get_params_keyboard_with_presets([], mode="marketplace")


def get_b2b_params_keyboard() -> InlineKeyboardMarkup:
    return get_params_keyboard_with_presets([], mode="b2b")


# Встроенные шаблоны МП — только ориентиры, НЕ официальные тарифы площадок.
# Комиссии зависят от категории, схемы (FBO/FBS), акций; логистика и упаковка — у каждого свои.
MP_TEMPLATES = {
    "wb": {
        "label": "WB (ориентир)",
        "commission_percent": 15.0,
        "logistics_cost": 80.0,
        "packaging_cost": 25.0,
        "tax_rate_percent": 6.0,
    },
    "ozon": {
        "label": "Ozon (ориентир)",
        "commission_percent": 15.0,
        "logistics_cost": 100.0,
        "packaging_cost": 30.0,
        "tax_rate_percent": 6.0,
    },
    "yam": {
        "label": "Я.Маркет (ориентир)",
        "commission_percent": 12.0,
        "logistics_cost": 90.0,
        "packaging_cost": 25.0,
        "tax_rate_percent": 6.0,
    },
}


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
        rows.append([
            InlineKeyboardButton(text="🟣 WB≈", callback_data="tpl_wb"),
            InlineKeyboardButton(text="🔵 Ozon≈", callback_data="tpl_ozon"),
            InlineKeyboardButton(text="🟡 ЯМ≈", callback_data="tpl_yam"),
        ])
    for pr in (presets or [])[:5]:
        rows.append([InlineKeyboardButton(
            text=f"📌 {pr.name}",
            callback_data=f"preset_{pr.id}",
        )])
    rows.append([InlineKeyboardButton(text="💱 Закуп в валюте (курс)", callback_data="fx_setup")])
    rows.append([InlineKeyboardButton(text="⚙️ Настроить вручную", callback_data="params_custom")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_price_col_keyboard(columns: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i, col in enumerate(columns[:10]):
        rows.append([InlineKeyboardButton(
            text=f"💰 {str(col)[:40]}",
            callback_data=f"price_col_{i}",
        )])
    rows.append([InlineKeyboardButton(text="➡️ Как определил бот", callback_data="price_col_auto")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")])
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
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
    ])


def get_run_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Рассчитать", callback_data="run_calc")],
        [InlineKeyboardButton(text="💾 Сохранить как пресет", callback_data="save_preset")],
        [InlineKeyboardButton(text="⚙️ Изменить параметры", callback_data="params_custom")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
    ])


def get_whatif_keyboard() -> InlineKeyboardMarkup:
    """Быстрый пересчёт «что если» по комиссии."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Комиссия −2%", callback_data="whatif_comm_-2"),
            InlineKeyboardButton(text="Комиссия +2%", callback_data="whatif_comm_+2"),
        ],
        [
            InlineKeyboardButton(text="Комиссия −5%", callback_data="whatif_comm_-5"),
            InlineKeyboardButton(text="Комиссия +5%", callback_data="whatif_comm_+5"),
        ],
        [InlineKeyboardButton(text="🔄 Новый расчёт", callback_data="new_calc")],
    ])


def get_skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить (0 ₽ / 0%)", callback_data="skip_param")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
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
    rows = [
        [
            InlineKeyboardButton(text="Комиссия −2%", callback_data="whatif_comm_-2"),
            InlineKeyboardButton(text="Комиссия +2%", callback_data="whatif_comm_+2"),
        ],
        [InlineKeyboardButton(text="🛒 Только к закупке (ROI≥30%)", callback_data="export_buy_list")],
        [InlineKeyboardButton(text="🔄 Новый расчёт", callback_data="new_calc")],
    ]
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


def get_mapping_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Всё верно", callback_data="map_ok")],
        [InlineKeyboardButton(text="✏️ Изменить колонки", callback_data="map_edit")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
    ])


def get_mapping_field_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Товар / название", callback_data="map_field_product")],
        [InlineKeyboardButton(text="Себестоимость / закуп", callback_data="map_field_cost")],
        [InlineKeyboardButton(text="Цена продажи (если есть)", callback_data="map_field_sell")],
        [InlineKeyboardButton(text="Количество", callback_data="map_field_qty")],
        [InlineKeyboardButton(text="Вес, кг", callback_data="map_field_weight")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="map_ok")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
    ])


def get_column_pick_keyboard(columns: list[str], prefix: str = "map_col") -> InlineKeyboardMarkup:
    rows = []
    for i, col in enumerate(columns[:20]):
        rows.append([InlineKeyboardButton(
            text=f"📌 {str(col)[:40]}",
            callback_data=f"{prefix}_{i}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="map_edit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
