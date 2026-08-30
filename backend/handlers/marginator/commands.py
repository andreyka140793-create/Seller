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
    get_help_keyboard,
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
    """Сброс FSM + temp-файлы."""
    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))
    for item in data.get("file_queue") or []:
        if isinstance(item, dict):
            _cleanup_temp_file(item.get("path"))
    await state.clear()


@marginator_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await _clear_state_and_cleanup(state)
    user = message.from_user
    is_new_flag: list = []
    if user:
        try:
            with SessionLocal() as db:
                MarginatorDBService.touch_user(
                    db,
                    user.id,
                    username=user.username,
                    full_name=user.full_name,
                    is_new_out=is_new_flag,
                )
        except Exception:
            pass
        try:
            from handlers.marginator.admin import notify_admins
            uname = ("@" + user.username) if user.username else (user.full_name or str(user.id))
            if is_new_flag and is_new_flag[0]:
                await notify_admins(
                    message.bot,
                    "🆕 Новый пользователь Маржинатора\n" + uname + " (" + str(user.id) + ")",
                )
        except Exception:
            pass
    text = "\n".join([
        "Маржинатор — маржа по прайсу за 3 шага.",
        "",
        "1) Демо или свой файл",
        "2) Параметры (или «Последние»)",
        "3) Рассчитать → Excel + мини-приложение",
        "",
        "/help · /terms · /demo",
    ])
    try:
        from handlers.marginator.admin import is_admin
        if user and is_admin(user.id):
            text += "\n\n🛠 Вам доступна /admin"
    except Exception:
        pass
    await message.answer(text, reply_markup=get_main_reply_keyboard())


@marginator_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "\n".join([
            "Помощь — Маржинатор",
            "",
            "Выберите раздел:",
        ]),
        reply_markup=get_help_keyboard(),
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
    await message.answer("Отменено.", reply_markup=get_main_reply_keyboard())


@marginator_router.message(F.text == "🔄 Новый расчёт")
async def btn_new_calc(message: Message, state: FSMContext):
    await _clear_state_and_cleanup(state)
    await message.answer("Режим расчёта:", reply_markup=get_mode_keyboard())
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
    await callback.message.answer("Режим расчёта:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.callback_query(F.data == "cancel_flow")
async def cb_cancel_flow(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _clear_state_and_cleanup(state)
    await callback.message.answer("Отменено.", reply_markup=get_main_reply_keyboard())



@marginator_router.callback_query(F.data == "help_howto")
async def help_howto(callback: CallbackQuery):
    await callback.answer()
    from services.marginator.legal_texts import howto_text
    await callback.message.answer(howto_text(), parse_mode="Markdown", reply_markup=get_help_keyboard())


@marginator_router.callback_query(F.data == "help_terms")
async def help_terms_cb(callback: CallbackQuery):
    await callback.answer()
    from services.marginator.analytics import AnalyticsService
    await callback.message.answer(
        AnalyticsService.glossary_message(),
        parse_mode="Markdown",
        reply_markup=get_help_keyboard(),
    )


@marginator_router.callback_query(F.data == "help_demo")
async def help_demo_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await cmd_demo(callback.message, state)


@marginator_router.callback_query(F.data == "help_privacy")
async def help_privacy(callback: CallbackQuery):
    await callback.answer()
    from services.marginator.legal_texts import PRIVACY_PARTS
    for i, part in enumerate(PRIVACY_PARTS):
        kw = {"parse_mode": "Markdown"}
        if i == len(PRIVACY_PARTS) - 1:
            kw["reply_markup"] = get_help_keyboard()
        await callback.message.answer(part, **kw)


@marginator_router.callback_query(F.data == "help_tos")
async def help_tos(callback: CallbackQuery):
    await callback.answer()
    from services.marginator.legal_texts import TOS_PARTS
    for i, part in enumerate(TOS_PARTS):
        kw = {"parse_mode": "Markdown"}
        if i == len(TOS_PARTS) - 1:
            kw["reply_markup"] = get_help_keyboard()
        await callback.message.answer(part, **kw)


@marginator_router.callback_query(F.data == "help_close")
async def help_close(callback: CallbackQuery):
    await callback.answer("Закрыто")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass



@marginator_router.message(F.text.in_({"🧪 Демо-прайс", "Демо-прайс", "/demo"}))
@marginator_router.message(Command("demo"))
async def cmd_demo(message: Message, state: FSMContext):
    """Онбординг: демо-файл → выбор режима."""
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
        {"Наименование": "Строка без цены (пропуск)", "Артикул": "ART-X", "Цена": 0, "Остаток": 1},
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    doc = BufferedInputFile(buf.read(), filename="demo_price.xlsx")
    await message.answer("Демо: 5 товаров. Дальше — режим → параметры → Рассчитать.")
    await message.answer_document(doc, caption="demo_price.xlsx")
    await message.answer("Шаг 1/3 — режим:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_marketplace")
async def select_mode_marketplace(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="marketplace")
    await callback.message.edit_text("Маркетплейс")
    await callback.message.answer("Шаг 1/3 — пришлите прайс:")
    await state.set_state(CalcState.upload_file)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_b2b")
async def select_mode_b2b(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="b2b")
    await callback.message.edit_text("B2B")
    await callback.message.answer("Шаг 1/3 — пришлите прайс:")
    await state.set_state(CalcState.upload_file)


@marginator_router.callback_query(CalcState.select_mode, F.data == "mode_compare")
async def select_mode_compare(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(calc_mode="compare")
    await callback.message.edit_text("Сравнение")
    await callback.message.answer("Прайс A:")
    await state.set_state(CalcState.compare_upload_a)

