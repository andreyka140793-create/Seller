"""Basic bot commands and text button handlers."""
from pathlib import Path
from aiogram import F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from states.marginator_states import CalcState
from keyboards.marginator_keyboards import (
    get_main_reply_keyboard, get_mode_keyboard, get_history_keyboard,
)
from database import SessionLocal
from services.marginator.db_service import MarginatorDBService


def _cleanup_temp_file(path: str | None) -> None:
    if not path:
        return
    try:
        p = Path(path)
        if p.is_file():
            p.unlink(missing_ok=True)
    except OSError:
        pass


async def _clear_state_and_cleanup(state: FSMContext) -> None:
    """Сбрасывает FSM-состояние и удаляет временный файл прайса, если он есть.
    Без этого при каждом новом расчёте поверх предыдущего файл на диске
    оставался бесхозным навсегда (накопление в /data/uploads)."""
    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))
    await state.clear()


@marginator_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await _clear_state_and_cleanup(state)
    await message.answer(
        "👋 Привет! Я Marginator — помогу рассчитать юнит-экономику товаров.\n\n"
        "Отправьте прайс-лист (Excel/CSV) или нажмите «🔄 Новый расчёт».",
        reply_markup=get_main_reply_keyboard(),
    )


@marginator_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 *Marginator — помощь*\n\n"
        "*Команды:*\n"
        "/start — начать\n"
        "/help — эта справка\n"
        "/history — история расчётов\n"
        "/cancel — отменить текущий расчёт\n\n"
        "*Как пользоваться:*\n"
        "1. Нажмите «🔄 Новый расчёт»\n"
        "2. Выберите режим (Маркетплейс / B2B)\n"
        "3. Отправьте файл прайса\n"
        "4. Проверьте распознанные колонки\n"
        "5. Настройте параметры и нажмите «Рассчитать»\n\n"
        "*Поддерживаемые форматы:* .xlsx, .xls, .csv, .txt, .docx, .pdf, .jpg, .png",
        parse_mode="Markdown",
    )


@marginator_router.message(Command("history"))
async def cmd_history(message: Message):
    from handlers.marginator.history import show_calculation_history
    await show_calculation_history(message, telegram_id=message.from_user.id)


@marginator_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    file_path = data.get("file_path")
    if file_path:
        from pathlib import Path
        Path(file_path).unlink(missing_ok=True)
    await state.clear()
    await message.answer("❌ Действие отменено.", reply_markup=get_main_reply_keyboard())


@marginator_router.message(F.text == "🔄 Новый расчёт")
async def btn_new_calc(message: Message, state: FSMContext):
    await _clear_state_and_cleanup(state)
    await message.answer(
        "Выберите режим расчёта:",
        reply_markup=get_mode_keyboard(),
    )
    await state.set_state(CalcState.select_mode)


@marginator_router.message(F.text == "📂 История")
async def btn_history(message: Message):
    from handlers.marginator.history import show_calculation_history
    await show_calculation_history(message, telegram_id=message.from_user.id)


@marginator_router.message(F.text == "📖 Помощь")
async def btn_help(message: Message):
    await cmd_help(message)


@marginator_router.message(F.text == "❌ Отмена")
async def btn_cancel(message: Message, state: FSMContext):
    await cmd_cancel(message, state)


@marginator_router.callback_query(F.data == "new_calc")
async def cb_new_calc(callback, state: FSMContext):
    await callback.answer()
    await _clear_state_and_cleanup(state)
    await callback.message.answer(
        "Выберите режим расчёта:",
        reply_markup=get_mode_keyboard(),
    )
    await state.set_state(CalcState.select_mode)


@marginator_router.callback_query(F.data == "cancel_flow")
async def cb_cancel_flow(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    file_path = data.get("file_path")
    if file_path:
        from pathlib import Path
        Path(file_path).unlink(missing_ok=True)
    await state.clear()
    await callback.message.answer("❌ Отменено.", reply_markup=get_main_reply_keyboard())


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_marketplace")
async def select_mode_marketplace(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="marketplace")
    await callback.message.edit_text("🛒 Режим: Маркетплейс (WB / Ozon)")
    await callback.message.answer("Отправьте файл прайса:")
    await state.set_state(CalcState.upload_file)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_b2b")
async def select_mode_b2b(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="b2b")
    await callback.message.edit_text("🏢 Режим: B2B (опт / НДС)")
    await callback.message.answer("Отправьте файл прайса:")
    await state.set_state(CalcState.upload_file)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_compare")
async def select_mode_compare(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="compare")
    await callback.message.edit_text("⚖️ Режим: Сравнение 2 прайсов")
    await callback.message.answer("Отправьте **первый** прайс (A):")
    await state.set_state(CalcState.compare_upload_a)
