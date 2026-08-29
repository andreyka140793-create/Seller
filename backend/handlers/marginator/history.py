"""History handlers."""
from pathlib import Path
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
            f"История расчётов ({len(uploads)}):\nНажмите файл — скачать Excel.",
            reply_markup=get_history_keyboard(uploads),
        )
        if len(uploads) >= 2:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            await message.answer(
                "Сравнить два последних расчёта?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="Сравнить 2 последних",
                        callback_data=f"hist_cmp_{uploads[0].id}_{uploads[1].id}",
                    )
                ]]),
            )


@marginator_router.callback_query(F.data.startswith("download_upload_"))
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
            caption=f"📦 Архивный отчёт: {filename}\n• Позиций: {len(items_data)}\n• Выручка: {total_revenue:,.2f} ₽\n• Прибыль: {total_profit:,.2f} ₽",
        )
    except Exception:
        await callback.message.answer("❌ Не удалось сформировать отчёт.")


@marginator_router.callback_query(F.data.startswith("hist_cmp_"))
async def history_compare_two(callback: CallbackQuery):
    """Сравнить цены/маржу двух сохранённых расчётов по названию товара."""
    await callback.answer("Сравниваю…")
    try:
        parts = (callback.data or "").replace("hist_cmp_", "").split("_")
        id_a, id_b = int(parts[0]), int(parts[1])
    except Exception:
        await callback.message.answer("Некорректные ID.")
        return
    try:
        import pandas as pd
        from aiogram.types import BufferedInputFile
        from services.marginator.exporter import ExcelExporterService
        with SessionLocal() as db:
            ua = MarginatorDBService.get_upload_with_items(db, id_a)
            ub = MarginatorDBService.get_upload_with_items(db, id_b)
            if not ua or not ub:
                await callback.message.answer("Расчёт не найден.")
                return
            if ua.user and int(ua.user.telegram_id) != int(callback.from_user.id):
                await callback.message.answer("Нет доступа.")
                return
            def to_df(u):
                rows = []
                for it in (u.items or []):
                    rows.append({
                        "Товар": (it.title or "").strip(),
                        "Цена": float(it.est_sell_price or 0),
                        "Маржа %": float(it.margin_pct or 0),
                        "Прибыль": float(it.net_profit or 0),
                    })
                return pd.DataFrame(rows)
            da, db_ = to_df(ua), to_df(ub)
        if da.empty or db_.empty:
            await callback.message.answer("В одном из расчётов нет позиций.")
            return
        merged = da.merge(db_, on="Товар", how="outer", suffixes=(" A", " B"))
        merged["Дельта цена"] = merged.get("Цена B", 0).fillna(0) - merged.get("Цена A", 0).fillna(0)
        merged["Дельта маржа %"] = merged.get("Маржа % B", 0).fillna(0) - merged.get("Маржа % A", 0).fillna(0)
        excel = ExcelExporterService.export_results_to_excel(
            merged.rename(columns={
                "Цена A": "Выручка, ₽",  # reuse exporter expects some cols - better raw write
            }) if False else merged
        )
        # simpler write
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            merged.to_excel(w, index=False, sheet_name="Сравнение")
        buf.seek(0)
        doc = BufferedInputFile(buf.read(), filename="history_compare.xlsx")
        await callback.message.answer_document(
            doc,
            caption=f"Сравнение: {ua.filename} vs {ub.filename}",
        )
    except Exception as e:
        await callback.message.answer(f"Ошибка сравнения: {type(e).__name__}: {e}")
