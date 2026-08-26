"""What-if recalculation handlers."""
from pathlib import Path
from aiogram import F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from handlers.marginator.calculation import execute_calculation_core, _load_file_bytes_from_state
from keyboards.marginator_keyboards import get_main_reply_keyboard


@marginator_router.callback_query(
    F.data.startswith("whatif_comm_") | F.data.startswith("whatif_log_") | F.data.startswith("whatif_tax_")
)
async def whatif_recalc(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "mapping" not in data and not data.get("file_path"):
        await callback.answer("Нет данных прошлого расчёта. Сделайте новый расчёт.", show_alert=True)
        return
    mode = data.get("calc_mode", "marketplace")
    if mode == "b2b":
        await callback.answer("«Что если» — только для маркетплейса.", show_alert=True)
        return

    raw = callback.data or ""
    label = ""
    try:
        if raw.startswith("whatif_comm_"):
            delta = float(raw.replace("whatif_comm_", "").replace("+", ""))
            old = float(data.get("commission_percent", 15) or 15)
            new = max(0.0, min(80.0, old + delta))
            await state.update_data(commission_percent=new)
            label = f"комиссией {old:g}% → {new:g}%"
        elif raw.startswith("whatif_log_"):
            delta = float(raw.replace("whatif_log_", "").replace("+", ""))
            if data.get("logistics_per_kg") is not None:
                old = float(data.get("logistics_per_kg") or 0)
                new = max(0.0, old + delta)
                await state.update_data(logistics_per_kg=new)
                label = f"логистикой {old:g} → {new:g} ₽/кг"
            else:
                old = float(data.get("logistics_cost", 0) or 0)
                new = max(0.0, old + delta)
                await state.update_data(logistics_cost=new)
                label = f"логистикой {old:g} → {new:g} ₽/шт"
        elif raw.startswith("whatif_tax_"):
            delta = float(raw.replace("whatif_tax_", "").replace("+", ""))
            old = float(data.get("tax_rate_percent", 6) or 6)
            new = max(0.0, min(50.0, old + delta))
            await state.update_data(tax_rate_percent=new)
            label = f"налогом {old:g}% → {new:g}%"
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return

    await callback.answer("Пересчёт…")
    await callback.message.answer(f"Пересчитываю с {label}…")

    file_bytes = await _load_file_bytes_from_state(data)
    if file_bytes:
        await execute_calculation_core(callback.message, state, data, file_bytes, callback.from_user.id)


@marginator_router.callback_query(F.data == "export_buy_list")
async def export_buy_list_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    path = data.get("last_results_path")
    if not path or not Path(path).is_file():
        await callback.answer("Нет последнего расчёта", show_alert=True)
        return
    await callback.answer("Формирую список…")
    try:
        import pandas as pd
        from aiogram.types import BufferedInputFile
        from services.marginator.exporter import ExcelExporterService
        df = pd.read_csv(path)
        raw = ExcelExporterService.export_buy_list(df, min_roi=30.0)
        n = len(df[df["ROI %"] >= 30]) if "ROI %" in df.columns else len(df)
        doc = BufferedInputFile(raw, filename="buy_list_roi30.xlsx")
        await callback.message.answer_document(doc, caption=f"🛒 К закупке (ROI ≥ 30%): ~{n} позиций.")
    except Exception:
        await callback.message.answer("Ошибка формирования списка.")
