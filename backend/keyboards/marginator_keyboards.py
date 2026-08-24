from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
    """Кнопка пропусков для опциональных шагов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить (0 ₽ / 0%)", callback_data="skip_param")]
        ]
    )
