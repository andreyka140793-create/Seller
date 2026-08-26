"""Preset handlers."""
from aiogram import F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from states.marginator_states import CalcState
from keyboards.marginator_keyboards import get_run_keyboard
from database import SessionLocal
from services.marginator.db_service import MarginatorDBService


@marginator_router.callback_query(F.data.startswith("preset_"))
async def apply_preset(callback: CallbackQuery, state: FSMContext):
    try:
        pid = int((callback.data or "").split("_")[1])
    except Exception:
        await callback.answer("Пресет не найден", show_alert=True)
        return
    with SessionLocal() as db:
        pr = MarginatorDBService.get_preset(db, callback.from_user.id, pid)
        if not pr:
            await callback.answer("Пресет не найден", show_alert=True)
            return
        await state.update_data(
            calc_mode=pr.calc_mode or "marketplace",
            commission_percent=pr.commission_percent,
            logistics_cost=pr.logistics_cost,
            packaging_cost=pr.packaging_cost,
            tax_rate_percent=pr.tax_rate_percent,
            tax_mode=pr.tax_mode or "usn_6",
            freight_cost=pr.freight_cost,
            manager_bonus_percent=pr.manager_bonus_percent,
            is_vat_included=pr.is_vat_included,
            vat_rate_percent=pr.vat_rate_percent,
            target_margin_percent=pr.target_margin_percent,
        )
    await callback.answer(f"Пресет «{pr.name}»")
    age_note = ""
    try:
        from datetime import datetime, timezone
        created = pr.created_at
        if created is not None:
            age_days = (datetime.now(timezone.utc) - created).days if created.tzinfo else (datetime.utcnow() - created).days
            if age_days >= 30:
                age_note = f"\n\n⚠️ Пресету {age_days} дн. — проверьте цифры."
    except Exception:
        pass
    await callback.message.edit_text(f"✅ Применён пресет: {pr.name}{age_note}")
    if pr.target_margin_percent is not None:
        await state.set_state(CalcState.confirm_params)
        await callback.message.answer(f"Цель маржинальности: {pr.target_margin_percent}%\nНажмите «Рассчитать».", reply_markup=get_run_keyboard())
    else:
        from handlers.marginator.params import prompt_target_margin
        await prompt_target_margin(callback.message, state)


@marginator_router.callback_query(F.data == "save_preset")
async def save_preset_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите название пресета (например, WB 18%):")
    await state.set_state(CalcState.save_preset_name)


@marginator_router.message(CalcState.save_preset_name)
async def save_preset_name(message, state: FSMContext):
    name = (message.text or "").strip()[:64]
    if not name:
        await message.answer("Название не должно быть пустым.")
        return
    data = await state.get_data()
    with SessionLocal() as db:
        MarginatorDBService.save_preset(db, message.from_user.id, name, data)
    await message.answer(f"💾 Пресет «{name}» сохранён.")
    await state.set_state(CalcState.confirm_params)
    await message.answer("Можно считать:", reply_markup=get_run_keyboard())
