"""FX rate handlers."""
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from states.marginator_states import CalcState
from handlers.marginator.params import prompt_target_margin


@marginator_router.callback_query(F.data == "fx_setup")
async def fx_setup_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="USD ЦБ", callback_data="fx_cbr_USD"),
         InlineKeyboardButton(text="CNY ЦБ", callback_data="fx_cbr_CNY"),
         InlineKeyboardButton(text="EUR ЦБ", callback_data="fx_cbr_EUR")],
        [InlineKeyboardButton(text="Уже рубли (×1)", callback_data="fx_cbr_RUB")],
    ])
    await callback.message.answer(
        "💱 Закуп в валюте\n\nЕсли в прайсе цены не в рублях — укажите курс.\n\n"
        "Примеры:\n• 95 — умножить закуп на 95\n• USD 92.5 — доллары по 92.5 ₽\n• CNY 13 — юани по 13 ₽\n• 1 — в файле уже рубли\n\n"
        "Или нажмите курс ЦБ РФ.",
        reply_markup=kb,
    )
    await state.set_state(CalcState.input_fx_rate)


@marginator_router.callback_query(CalcState.input_fx_rate, F.data.startswith("fx_cbr_"))
async def fx_cbr_pick(callback: CallbackQuery, state: FSMContext):
    code = (callback.data or "").replace("fx_cbr_", "").upper()
    if code == "RUB":
        await state.update_data(fx_rate=1.0, fx_code="RUB")
        await callback.answer("Без пересчёта")
        await callback.message.answer("✅ Курс ×1 — цены уже в рублях.")
        await prompt_target_margin(callback.message, state)
        return
    try:
        from services.marginator.fx_cbr import get_cbr_rate
        rate = get_cbr_rate(code)
    except Exception:
        await callback.answer("ЦБ недоступен", show_alert=True)
        return
    if rate is None:
        await callback.answer("Нет курса", show_alert=True)
        return
    await state.update_data(fx_rate=float(rate), fx_code=code)
    await callback.answer(f"{code} {rate}")
    await callback.message.answer(f"✅ Курс ЦБ: 1 {code} = {rate} ₽")
    await prompt_target_margin(callback.message, state)


@marginator_router.message(CalcState.input_fx_rate)
async def process_fx_rate(message, state: FSMContext):
    import re as _re
    raw = (message.text or "").strip().upper().replace(",", ".")
    code = "FX"
    m = _re.match(r"([A-Z]{3})\s*([0-9]+(?:\.[0-9]+)?)", raw)
    if m:
        code, rate_s = m.group(1), m.group(2)
        rate = float(rate_s)
    else:
        m2 = _re.search(r"[0-9]+(?:\.[0-9]+)?", raw)
        if not m2:
            await message.answer("Не понял курс. Примеры: 92.5 или USD 92.5 или CNY 13")
            return
        rate = float(m2.group(0))
    if rate <= 0:
        await message.answer("Курс должен быть больше 0.")
        return
    await state.update_data(fx_rate=rate, fx_code=code)
    await message.answer(f"✅ Курс принят: 1 {code} = {rate} ₽")
    await prompt_target_margin(message, state)
