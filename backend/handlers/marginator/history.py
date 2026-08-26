"""History handlers."""
from aiogram import F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from keyboards.marginator_keyboards import get_history_keyboard, get_main_reply_keyboard
from database import SessionLocal
from services.marginator.db_service import MarginatorDBService


@marginator_router.callback_query(F.data == "show_history")
async def show_history_cb(callback: CallbackQuery):
    await callback.answer()
    await show_calculation_history(callback.message, telegram_id=callback.from_user.id)


async def show_calculation_history(message, telegram_id: int | None = None):
    uid = int(telegram_id) if telegram_id is not None else int(message.from_user.id)
    with SessionLocal() as db:
        uploads = MarginatorDBService.get_user_history(db, uid)
        if not uploads:
            await message.answer("📂 У вас пока нет сохранённых расчётов.")
            return
        await message.answer(
            f"📜 История расчётов ({len(uploads)}):
Нажмите на файл — пришлю Excel повторно.",
            reply_markup=get_history_keyboard(uploads),
        )


@marginator_router.callback_query(F.data.startswith("download_upload_") | F.data.startswith("hist_"))
async def download_archived_report(callback: CallbackQuery):
    try:
        await callback.answer("Готовлю Excel…")
    except Exception:
        pass
    try:
        raw = callback.data or ""
        upload_id = int(raw.rsplit("_", 1)[-1])
    except Exception:
        await callback.message.answer("❌ Некорректный ID.")
        return

    try:
        import pandas as pd
        from aiogram.types import BufferedInputFile
        from services.marginator.exporter import ExcelExporterService
        with SessionLocal() as db:
            upload = MarginatorDBService.get_upload_with_items(db, upload_id)
            if not upload:
                await callback.message.answer("❌ Расчёт не найден.")
                return
            owner_id = upload.user.telegram_id if upload.user else None
            if owner_id is not None and int(owner_id) != int(callback.from_user.id):
                await callback.message.answer("❌ Этот расчёт вам не принадлежит.")
                return
            items = list(upload.items or [])
            if not items:
                await callback.message.answer("❌ В расчёте нет позиций.")
                return
            items_data = [
                {
                    "Товар": item.title,
                    "Себестоимость, ₽": item.buy_price,
                    "Выручка, ₽": item.est_sell_price,
                    "Чистая прибыль, ₽": item.net_profit,
                    "Маржинальность %": item.margin_pct,
                    "Рентабельность чистая %": item.margin_pct,
                    "ROI %": item.roi_pct,
                }
                for item in items
            ]
            filename = upload.filename or "price.xlsx"
            total_revenue = float(upload.total_revenue or 0)
            total_profit = float(upload.total_profit or 0)

        df_results = pd.DataFrame(items_data)
        excel_bytes = ExcelExporterService.export_results_to_excel(df_results)
        out_name = Path(filename).stem + "_marginator.xlsx"
        document = BufferedInputFile(excel_bytes, filename=out_name)
        await callback.message.answer_document(
            document=document,
            caption=f"📦 Архивный отчёт: {filename}
• Позиций: {len(items_data)}
• Выручка: {total_revenue:,.2f} ₽
• Прибыль: {total_profit:,.2f} ₽",
        )
    except Exception:
        await callback.message.answer("❌ Не удалось сформировать отчёт.")
