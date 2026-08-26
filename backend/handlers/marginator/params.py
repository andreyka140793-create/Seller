"""Parameter input handlers (commission, logistics, packaging, tax, freight, etc.)."""
from aiogram import F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from states.marginator_states import CalcState
from keyboards.marginator_keyboards import (
    get_skip_keyboard, get_target_margin_keyboard, get_run_keyboard,
    get_main_reply_keyboard,
)
from config import PurchasingConfig


@marginator_router.callback_query(CalcState.input_commission, F.data == "params_default")
async def params_default(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")
    if mode == "b2b":
        await state.update_data(
            freight_cost=0.0, manager_bonus_percent=0.0,
            is_vat_included=True, vat_rate_percent=20.0,
        )
    else:
        await state.update_data(
            commission_percent=PurchasingConfig.DEFAULT_MP_COMMISSION_PCT,
            logistics_cost=PurchasingConfig.DEFAULT_LOGISTICS_RUB,
            packaging_cost=PurchasingConfig.DEFAULT_PACKAGING_RUB,
            tax_rate_percent=PurchasingConfig.DEFAULT_TAX_PCT,
            tax_mode="usn_6",
        )
    await callback.message.edit_text("✅ Применены значения по умолчанию.")
    await prompt_target_margin(callback.message, state)


@marginator_router.callback_query(CalcState.input_commission, F.data == "params_custom")
async def params_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")
    if mode == "b2b":
        await callback.message.answer("Введите фрахт за единицу (₽) или 0:")
        await state.set_state(CalcState.input_freight)
    else:
        await callback.message.answer("Введите комиссию маркетплейса (%):")
        await state.set_state(CalcState.input_commission)


@marginator_router.callback_query(CalcState.input_commission, F.data.startswith("tpl_"))
async def params_template(callback: CallbackQuery, state: FSMContext):
    tpl = (callback.data or "").replace("tpl_", "")
    from keyboards.marginator_keyboards import MP_TEMPLATES
    t = MP_TEMPLATES.get(tpl)
    if not t:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await callback.answer(t["label"])
    await state.update_data(
        commission_percent=t["commission_percent"],
        logistics_cost=t["logistics_cost"],
        packaging_cost=t["packaging_cost"],
        tax_rate_percent=t["tax_rate_percent"],
        tax_mode=t["tax_mode"],
    )
    await callback.message.edit_text(f"✅ Применён шаблон: {t['label']}")
    await prompt_target_margin(callback.message, state)


@marginator_router.message(CalcState.input_commission)
async def input_commission(message: Message, state: FSMContext):
    try:
        val = float((message.text or "").replace(",", ".").replace("%", "").strip())
        val = max(0.0, min(80.0, val))
    except ValueError:
        await message.answer("Введите число (0–80).")
        return
    await state.update_data(commission_percent=val)
    await message.answer("Введите логистику (₽/шт):")
    await state.set_state(CalcState.input_logistics)


@marginator_router.message(CalcState.input_logistics)
async def input_logistics(message: Message, state: FSMContext):
    try:
        val = float((message.text or "").replace(",", ".").replace("₽", "").strip())
        val = max(0.0, val)
    except ValueError:
        await message.answer("Введите число >= 0.")
        return
    await state.update_data(logistics_cost=val)
    await message.answer("Введите упаковку (₽/шт):")
    await state.set_state(CalcState.input_packaging)


@marginator_router.message(CalcState.input_packaging)
async def input_packaging(message: Message, state: FSMContext):
    try:
        val = float((message.text or "").replace(",", ".").replace("₽", "").strip())
        val = max(0.0, val)
    except ValueError:
        await message.answer("Введите число >= 0.")
        return
    await state.update_data(packaging_cost=val)
    await message.answer("Введите налог (%). Пропустить — УСН 6%:", reply_markup=get_skip_keyboard())
    await state.set_state(CalcState.input_tax)


@marginator_router.callback_query(CalcState.input_tax, F.data == "skip_param")
async def skip_tax(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(tax_rate_percent=PurchasingConfig.DEFAULT_TAX_PCT, tax_mode="usn_6")
    await prompt_target_margin(callback.message, state)


@marginator_router.message(CalcState.input_tax)
async def input_tax(message: Message, state: FSMContext):
    try:
        val = float((message.text or "").replace(",", ".").replace("%", "").strip())
        val = max(0.0, min(50.0, val))
    except ValueError:
        await message.answer("Введите число (0–50).")
        return
    await state.update_data(tax_rate_percent=val, tax_mode="usn_6")
    await prompt_target_margin(message, state)


@marginator_router.message(CalcState.input_freight)
async def input_freight(message: Message, state: FSMContext):
    try:
        val = float((message.text or "").replace(",", ".").replace("₽", "").strip())
        val = max(0.0, val)
    except ValueError:
        await message.answer("Введите число >= 0.")
        return
    await state.update_data(freight_cost=val)
    await message.answer("Введите бонус менеджера (%). Пропустить — 0%:", reply_markup=get_skip_keyboard())
    await state.set_state(CalcState.input_manager_bonus)


@marginator_router.callback_query(CalcState.input_manager_bonus, F.data == "skip_param")
async def skip_manager_bonus(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(manager_bonus_percent=0.0)
    await prompt_target_margin(callback.message, state)


@marginator_router.message(CalcState.input_manager_bonus)
async def input_manager_bonus(message: Message, state: FSMContext):
    try:
        val = float((message.text or "").replace(",", ".").replace("%", "").strip())
        val = max(0.0, min(50.0, val))
    except ValueError:
        await message.answer("Введите число (0–50).")
        return
    await state.update_data(manager_bonus_percent=val)
    await prompt_target_margin(message, state)


async def prompt_target_margin(message, state: FSMContext):
    await message.answer("🎯 Целевая маржинальность? (по умолчанию — без ограничений)", reply_markup=get_target_margin_keyboard())
    await state.set_state(CalcState.input_target_margin)


@marginator_router.callback_query(CalcState.input_target_margin, F.data.startswith("target_m_"))
async def target_margin_cb(callback: CallbackQuery, state: FSMContext):
    raw = (callback.data or "").replace("target_m_", "")
    if raw == "skip":
        await state.update_data(target_margin_percent=None)
    else:
        try:
            await state.update_data(target_margin_percent=float(raw))
        except ValueError:
            await state.update_data(target_margin_percent=None)
    await callback.answer()
    await show_params_summary(callback.message, state)


@marginator_router.message(CalcState.input_target_margin)
async def target_margin_text(message: Message, state: FSMContext):
    try:
        val = float((message.text or "").replace(",", ".").replace("%", "").strip())
        val = max(0.0, min(95.0, val))
        await state.update_data(target_margin_percent=val)
    except ValueError:
        await state.update_data(target_margin_percent=None)
    await show_params_summary(message, state)


async def show_params_summary(message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")
    lines = ["⚙️ Параметры расчёта:"]
    if mode == "b2b":
        lines.append(f"• Фрахт: {data.get('freight_cost', 0)} ₽/шт")
        lines.append(f"• Бонус менеджера: {data.get('manager_bonus_percent', 0)}%")
        lines.append(f"• НДС: {'включён' if data.get('is_vat_included', True) else 'не включён'} ({data.get('vat_rate_percent', 20)}%)")
    else:
        lines.append(f"• Комиссия: {data.get('commission_percent', 15)}%")
        lines.append(f"• Логистика: {data.get('logistics_cost', 120)} ₽/шт")
        lines.append(f"• Упаковка: {data.get('packaging_cost', 30)} ₽/шт")
        lines.append(f"• Налог: {data.get('tax_rate_percent', 6)}% ({data.get('tax_mode', 'usn_6')})")
    tgt = data.get("target_margin_percent")
    if tgt is not None:
        lines.append(f"• Целевая маржа: {tgt}%")
    fx = data.get("fx_rate")
    if fx and fx != 1.0:
        lines.append(f"• Курс: ×{fx} ({data.get('fx_code', 'FX')})")
    lines.append("\nНажмите «Рассчитать» или «Изменить параметры».")
    await message.answer("\n".join(lines), reply_markup=get_run_keyboard())
    await state.set_state(CalcState.confirm_params)
