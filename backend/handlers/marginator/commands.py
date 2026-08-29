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
    """Сброс FSM + удаление текущего файла и всей очереди с диска."""
    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))
    for item in data.get("file_queue") or []:
        if isinstance(item, dict):
            _cleanup_temp_file(item.get("path"))
    await state.clear()


@marginator_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await _clear_state_and_cleanup(state)
    await message.answer(
        "Привет! Я Marginator — юнит-экономика по прайсу.\n\n"
        "Отправьте прайс или «Новый расчёт».\n"
        "Термины: /terms",
        reply_markup=get_main_reply_keyboard(),
    )


@marginator_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "\n".join([
            "Marginator — помощь",
            "",
            "Команды:",
            "/start — начать",
            "/help — эта справка",
            "/history — история расчётов",
            "/terms — маржа, наценка, ROI",
            "/cancel — отменить",
            "",
            "Как пользоваться:",
            "1. Новый расчёт",
            "2. Режим Маркетплейс / B2B",
            "3. Файл прайса",
            "4. Проверка колонок",
            "5. Параметры и Рассчитать",
            "",
            "Форматы: Excel, CSV, YML/XML, PDF, Word, JSON, фото, ZIP",
            "В Excel первый лист — Справка по терминам.",
        ])
    )


@marginator_router.message(Command("terms"))
@marginator_router.message(F.text.in_({"📖 Термины", "Термины", "/terms"}))
async def cmd_terms(message: Message):
    from services.marginator.analytics import AnalyticsService
    await message.answer(AnalyticsService.glossary_message(), parse_mode="Markdown")


@marginator_router.message(Command("history"))
async def cmd_history(message: Message):
    from handlers.marginator.history import show_calculation_history
    await show_calculation_history(message, telegram_id=message.from_user.id)


@marginator_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))
    for item in data.get("file_queue") or []:
        if isinstance(item, dict):
            _cleanup_temp_file(item.get("path"))
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_reply_keyboard())


@marginator_router.message(F.text == "🔄 Новый расчёт")
async def btn_new_calc(message: Message, state: FSMContext):
    await _clear_state_and_cleanup(state)
    await message.answer("Выберите режим расчёта:", reply_markup=get_mode_keyboard())
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
    await callback.message.answer("Выберите режим расчёта:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.callback_query(F.data == "cancel_flow")
async def cb_cancel_flow(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _clear_state_and_cleanup(state)
    await callback.message.answer("Отменено.", reply_markup=get_main_reply_keyboard())




@marginator_router.message(F.text.in_({"🧪 Демо-прайс", "Демо-прайс", "/demo"}))
@marginator_router.message(Command("demo"))
async def cmd_demo(message: Message, state: FSMContext):
    """Короткий онбординг: демо-файл + сразу режим маркетплейса."""
    await _clear_state_and_cleanup(state)
    import io
    import pandas as pd
    from aiogram.types import BufferedInputFile
    rows = [
        {"Наименование": "Дрель Bosch GSB 13 RE", "Артикул": "ART-1001", "Цена": 3490, "Остаток": 12},
        {"Наименование": "Шуруповёрт Makita DF333D", "Артикул": "ART-1002", "Цена": 5290, "Остаток": 8},
        {"Наименование": "Перфоратор DeWalt D25133K", "Артикул": "ART-1003", "Цена": 8990, "Остаток": 5},
        {"Наименование": "Уровень лазерный Huepar", "Артикул": "ART-1004", "Цена": 4150, "Остаток": 20},
        {"Наименование": "Набор бит 50 шт", "Артикул": "ART-1005", "Цена": 890, "Остаток": 100},
        {"Наименование": "Товар без цены (будет пропущен)", "Артикул": "ART-X", "Цена": 0, "Остаток": 1},
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    doc = BufferedInputFile(buf.read(), filename="demo_price.xlsx")
    await message.answer(
        "Демо-прайс: 5 товаров + 1 пустая цена (её бот пропустит).\n"
        "Дальше: режим → параметры → Рассчитать.\n"
        "Или просто перешлите этот файл боту после выбора режима."
    )
    await message.answer_document(doc, caption="demo_price.xlsx")
    await message.answer("Выберите режим:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_marketplace")
async def select_mode_marketplace(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="marketplace")
    await callback.message.edit_text("Режим: Маркетплейс (WB / Ozon)")
    await callback.message.answer("Отправьте файл прайса:")
    await state.set_state(CalcState.upload_file)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_b2b")
async def select_mode_b2b(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="b2b")
    await callback.message.edit_text("Режим: B2B (опт / НДС)")
    await callback.message.answer("Отправьте файл прайса:")
    await state.set_state(CalcState.upload_file)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_compare")
async def select_mode_compare(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="compare")
    await callback.message.edit_text("Режим: Сравнение 2 прайсов")
    await callback.message.answer("Отправьте первый прайс (A):")
    await state.set_state(CalcState.compare_upload_a)
