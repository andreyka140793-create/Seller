"""Core calculation execution (heavy CPU work in thread pool)."""
import os
import asyncio
from pathlib import Path
import pandas as pd
from aiogram import F
from aiogram.types import Message, BufferedInputFile
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from states.marginator_states import CalcState
from keyboards.marginator_keyboards import get_main_reply_keyboard, get_webapp_keyboard
from database import SessionLocal
from services.marginator.parser import ExcelParserService
from services.marginator.calculators import MarketplaceCalculator, B2BCalculator, MarketplaceParams, B2BParams, BaseItem, min_selling_price_for_margin
from services.marginator.analytics import AnalyticsService
from services.marginator.exporter import ExcelExporterService
from services.marginator.db_service import MarginatorDBService
from services.marginator.utils import clean_numeric_value
from services.marginator.file_io import resolve_column, detect_columns_by_keywords, detect_weight_column
from config import PurchasingConfig

_UPLOAD_ROOT = Path("/data/uploads") if os.path.exists("/data") else Path(__import__("tempfile").gettempdir()) / "marginator_uploads"


def _cleanup_temp_file(path: str | None) -> None:
    if not path:
        return
    try:
        p = Path(path)
        if p.is_file():
            p.unlink(missing_ok=True)
    except OSError:
        pass


async def _load_file_bytes_from_state(data: dict) -> bytes | None:
    path = data.get("file_path")
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    return p.read_bytes()


@marginator_router.message(CalcState.confirm_params, F.text.in_({"🚀 Рассчитать", "Рассчитать", "посчитать", "Посчитать"}))
@marginator_router.callback_query(F.data == "run_calc")
async def execute_calculation_trigger(update, state: FSMContext):
    if hasattr(update, "message"):
        message = update.message
        user = update.from_user
        await update.answer("Считаю…")
    else:
        message = update
        user = update.from_user

    data = await state.get_data()
    file_bytes = await _load_file_bytes_from_state(data)
    if file_bytes is None or "mapping" not in data:
        from handlers.marginator.upload import fail_current_keep_queue, queue_continue_keyboard
        qlen = await fail_current_keep_queue(state, cleanup_path=True)
        if qlen:
            await message.answer(
                f"❌ Данные файла потеряны. Очередь сохранена ({qlen}).",
                reply_markup=queue_continue_keyboard(qlen),
            )
        else:
            await message.answer(
                "❌ Данные файла потеряны. Нажмите «🔄 Новый расчёт».",
                reply_markup=get_main_reply_keyboard(),
            )
        return

    await execute_calculation_core(message, state, data, file_bytes, user.id)


async def execute_calculation_core(message, state: FSMContext, data: dict, file_bytes: bytes, user_id: int):
    file_name = data.get("file_name", "price.xlsx")
    raw_mapping = data["mapping"]
    from services.marginator.schemas import TableMappingSchema
    mapping = TableMappingSchema.model_validate(raw_mapping) if isinstance(raw_mapping, dict) else raw_mapping
    calc_mode = data.get("calc_mode", "marketplace")

    status_msg = await message.answer("📊 **Считаю юнит-экономику...**", parse_mode="Markdown")

    try:
        parser = ExcelParserService(api_key=os.getenv("XAI_API_KEY"))
        df = await asyncio.to_thread(parser.load_normalized_dataframe, file_bytes, file_name, mapping)
    except Exception:
        await status_msg.edit_text("❌ Ошибка чтения файла. Сохраните как .xlsx и попробуйте снова.")
        return

    product_col = resolve_column(df, mapping.product_name_col)
    cost_col = resolve_column(df, mapping.cost_price_col)
    sell_col = resolve_column(df, mapping.selling_price_col)
    qty_col = resolve_column(df, mapping.quantity_col)

    detected = detect_columns_by_keywords(df)

    def _col_is_not_price(name: str | None) -> bool:
        if not name:
            return True
        n = str(name).strip().lower().replace("ё", "е")
        if n in ("ед", "ед.", "ед.изм", "ед. изм.", "unit", "uom"):
            return True
        if n.startswith("ед.") or n.startswith("ед "):
            return True
        bad = ("артикул", "sku", "barcode", "штрих", "категор", "бренд", "наимен", "назван", "остаток", "кол. в", "кол в уп")
        return any(b in n for b in bad)

    if not product_col:
        product_col = detected.get("product_name_col")
    if not cost_col or _col_is_not_price(cost_col):
        cost_col = detected.get("cost_price_col") or cost_col
    if _col_is_not_price(cost_col):
        for c in df.columns:
            cn = str(c).lower()
            if any(x in cn for x in ("руб", "р.", "₽", "price")) and not _col_is_not_price(str(c)):
                cost_col = str(c)
                break
    if not sell_col:
        sell_col = detected.get("selling_price_col")
    if not qty_col:
        qty_col = detected.get("quantity_col")

    if not product_col or not cost_col:
        await status_msg.edit_text("❌ Не найдены нужные колонки. Проверьте файл и попробуйте снова.")
        from handlers.marginator.upload import fail_current_keep_queue, queue_continue_keyboard
        qlen = await fail_current_keep_queue(state, cleanup_path=True)
        if qlen:
            await message.answer(
                f"Очередь сохранена ({qlen}). Можно взять следующий файл.",
                reply_markup=queue_continue_keyboard(qlen),
            )
        return

    fx_rate = data.get("fx_rate", 1.0) or 1.0

    results = []
    if calc_mode == "b2b":
        results = await _calc_b2b(df, product_col, cost_col, sell_col, qty_col, data, fx_rate, status_msg)
    else:
        results = await _calc_marketplace(df, product_col, cost_col, sell_col, qty_col, data, fx_rate, status_msg)

    if not results:
        await status_msg.edit_text("❌ Не удалось рассчитать ни одной позиции. Проверьте, что в колонке цены есть числа > 0.")
        from handlers.marginator.upload import fail_current_keep_queue, queue_continue_keyboard
        qlen = await fail_current_keep_queue(state, cleanup_path=True)
        if qlen:
            await message.answer(
                f"Очередь сохранена ({qlen}). Можно взять следующий файл.",
                reply_markup=queue_continue_keyboard(qlen),
            )
        return

    df_results = pd.DataFrame(results)

    try:
        last_path = _UPLOAD_ROOT / f"last_results_{user_id}.csv"
        await asyncio.to_thread(df_results.to_csv, last_path, index=False)
        await state.update_data(last_results_path=str(last_path))
    except Exception:
        pass

    with SessionLocal() as db:
        upload_record = await asyncio.to_thread(
            MarginatorDBService.save_calculation_results,
            db, user_id, file_name, calc_mode, df_results
        )
        upload_id = upload_record.id

    summary = await asyncio.to_thread(AnalyticsService.generate_summary, df_results)
    summary_text = AnalyticsService.format_summary_message(summary)
    excel_bytes = await asyncio.to_thread(ExcelExporterService.export_results_to_excel, df_results)
    out_name = Path(file_name).stem + "_marginator.xlsx"
    document = BufferedInputFile(excel_bytes, filename=out_name)

    await status_msg.delete()
    await message.answer(summary_text, reply_markup=get_webapp_keyboard(upload_id), parse_mode="Markdown")
    await message.answer_document(document=document, caption="📥 Полный Excel-файл со всеми позициями.", parse_mode="Markdown")

    await state.update_data(last_upload_id=upload_id, upload_busy=False)
    await state.set_state(CalcState.confirm_params)

    # Очередь следующих прайсов
    data_after = await state.get_data()
    if data_after.get("file_queue"):
        n = len(data_after["file_queue"])
        await message.answer(
            f"📋 В очереди ещё {n} файл(ов).\n"
            "Нажмите «➡️ Следующий файл» или «🔄 Новый расчёт».",
            reply_markup=__import__(
                "aiogram.types", fromlist=["InlineKeyboardMarkup", "InlineKeyboardButton"]
            ).InlineKeyboardMarkup(
                inline_keyboard=[
                    [__import__("aiogram.types", fromlist=["InlineKeyboardButton"]).InlineKeyboardButton(
                        text="➡️ Следующий файл из очереди",
                        callback_data="queue_next",
                    )],
                ]
            ),
        )


async def _calc_marketplace(df, product_col, cost_col, sell_col, qty_col, data, fx_rate, status_msg):
    calc = MarketplaceCalculator()
    commission_percent = float(data.get("commission_percent", PurchasingConfig.DEFAULT_MP_COMMISSION_PCT))
    logistics_cost = float(data.get("logistics_cost", PurchasingConfig.DEFAULT_LOGISTICS_RUB))
    packaging_cost = float(data.get("packaging_cost", PurchasingConfig.DEFAULT_PACKAGING_RUB))
    tax_rate_percent = float(data.get("tax_rate_percent", PurchasingConfig.DEFAULT_TAX_PCT))
    tax_mode = data.get("tax_mode", "usn_6")
    logistics_per_kg = data.get("logistics_per_kg")

    total_rows = len(df)
    results = []
    for n, (_, row) in enumerate(df.iterrows(), 1):
        try:
            raw_name = row.get(product_col)
            product_name = str(raw_name).strip() if pd.notna(raw_name) else ""
            if not product_name or product_name.lower() in ("название", "наименование", "товар", "none", "nan"):
                continue
            cost_price = clean_numeric_value(row.get(cost_col))
            if cost_price <= 0:
                continue
            cost_price = cost_price * fx_rate

            qty = 1
            if qty_col:
                q = clean_numeric_value(row.get(qty_col))
                if q >= 1:
                    qty = int(q)

            weight_kg = None
            wcol = data.get("mapping", {})
            if isinstance(wcol, dict):
                wcol = wcol.get("weight_col")
            else:
                wcol = getattr(wcol, "weight_col", None) if wcol else None
            if not wcol:
                wcol = detect_weight_column(df)
            if wcol:
                wcol_r = resolve_column(df, wcol)
                if wcol_r:
                    wv = clean_numeric_value(row.get(wcol_r))
                    if wv > 0:
                        if wv > 50 and "кг" not in str(wcol_r).lower() and "kg" not in str(wcol_r).lower():
                            weight_kg = wv / 1000.0
                        else:
                            weight_kg = wv

            if sell_col:
                selling_price = clean_numeric_value(row.get(sell_col))
                if selling_price <= 0:
                    selling_price = cost_price * (1 + PurchasingConfig.DEFAULT_MARKUP_PCT / 100.0)
            else:
                selling_price = cost_price * (1 + PurchasingConfig.DEFAULT_MARKUP_PCT / 100.0)

            item = BaseItem(product_name=product_name, cost_price=cost_price, quantity=qty, weight_kg=weight_kg)
            params = MarketplaceParams(
                selling_price=selling_price,
                commission_percent=commission_percent,
                logistics_cost=logistics_cost,
                logistics_per_kg=logistics_per_kg,
                packaging_cost=packaging_cost,
                tax_rate_percent=tax_rate_percent,
                tax_mode=tax_mode,
            )
            res = calc.calculate_item(item, params)
            row_out = {
                "Товар": item.product_name,
                "Себестоимость, ₽": item.cost_price,
                "Выручка, ₽": res.revenue,
                "Переменные расходы, ₽": res.variable_costs,
                "Маржа, ₽": res.margin_rub,
                "Маржинальность %": res.margin_percent,
                "Наценка %": res.markup_percent,
                "Чистая прибыль, ₽": res.net_profit,
                "Рентабельность чистая %": res.net_margin_percent,
                "ROI %": res.roi_percent,
            }
            tgt = data.get("target_margin_percent")
            if tgt is not None:
                mp = min_selling_price_for_margin(
                    item.cost_price,
                    target_margin_percent=float(tgt),
                    commission_percent=commission_percent,
                    logistics_cost=logistics_cost,
                    packaging_cost=packaging_cost,
                )
                if mp is not None:
                    row_out[f"Мин. цена для маржи {tgt:g}%"] = mp
            results.append(row_out)

            if n == 1 or n % 250 == 0 or n == total_rows:
                try:
                    await status_msg.edit_text(f"📊 Считаю… {n}/{total_rows}")
                except Exception:
                    pass
        except Exception:
            continue
    return results


async def _calc_b2b(df, product_col, cost_col, sell_col, qty_col, data, fx_rate, status_msg):
    calc = B2BCalculator()
    freight = float(data.get("freight_cost", 0.0))
    bonus = float(data.get("manager_bonus_percent", 0.0))
    vat = bool(data.get("is_vat_included", True))
    vat_rate = float(data.get("vat_rate_percent", 20.0))

    total_rows = len(df)
    results = []
    for n, (_, row) in enumerate(df.iterrows(), 1):
        try:
            raw_name = row.get(product_col)
            product_name = str(raw_name).strip() if pd.notna(raw_name) else ""
            if not product_name or product_name.lower() in ("название", "наименование", "товар", "none", "nan"):
                continue
            cost_price = clean_numeric_value(row.get(cost_col))
            if cost_price <= 0:
                continue
            cost_price = cost_price * fx_rate

            qty = 1
            if qty_col:
                q = clean_numeric_value(row.get(qty_col))
                if q >= 1:
                    qty = int(q)

            if sell_col:
                wholesale = clean_numeric_value(row.get(sell_col))
                if wholesale <= 0:
                    wholesale = cost_price * (1 + PurchasingConfig.DEFAULT_MARKUP_PCT / 100.0)
            else:
                wholesale = cost_price * (1 + PurchasingConfig.DEFAULT_MARKUP_PCT / 100.0)

            item = BaseItem(product_name=product_name, cost_price=cost_price, quantity=qty)
            params = B2BParams(
                wholesale_price=wholesale,
                freight_cost_per_unit=freight,
                manager_bonus_percent=bonus,
                is_vat_included=vat,
                vat_rate_percent=vat_rate,
            )
            res = calc.calculate_item(item, params)
            results.append({
                "Товар": item.product_name,
                "Себестоимость, ₽": item.cost_price,
                "Выручка, ₽": res.revenue,
                "Переменные расходы, ₽": res.variable_costs,
                "Маржа, ₽": res.margin_rub,
                "Маржинальность %": res.margin_percent,
                "Наценка %": res.markup_percent,
                "Чистая прибыль, ₽": res.net_profit,
                "Рентабельность чистая %": res.net_margin_percent,
                "ROI %": res.roi_percent,
            })

            if n == 1 or n % 250 == 0 or n == total_rows:
                try:
                    await status_msg.edit_text(f"📊 Считаю… {n}/{total_rows}")
                except Exception:
                    pass
        except Exception:
            continue
    return results
