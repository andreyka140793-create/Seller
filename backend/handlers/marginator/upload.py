"""File upload and mapping handlers."""
import os
import tempfile
from pathlib import Path

import pandas as pd
from aiogram import F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from states.marginator_states import CalcState
from keyboards.marginator_keyboards import (
    get_mode_keyboard, get_mapping_confirm_keyboard, get_main_reply_keyboard,
    get_params_keyboard_with_presets, get_price_col_keyboard,
    get_mapping_field_keyboard, get_column_pick_keyboard,
)
from database import SessionLocal
from services.marginator.parser import ExcelParserService
from services.marginator.file_io import read_table, detect_columns_by_keywords
from services.marginator.schemas import TableMappingSchema
from services.marginator.utils import sanitize_filename
from config import PurchasingConfig

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
_UPLOAD_ROOT = Path("/data/uploads") if os.path.exists("/data") else Path(tempfile.gettempdir()) / "marginator_uploads"
_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


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


@marginator_router.message(CalcState.upload_file, F.document)
async def handle_file_upload(message: Message, state: FSMContext, bot: Bot):
    document = message.document
    file_name = (document.file_name or "price.xlsx").lower()

    from services.marginator.document_loader import is_supported
    if not is_supported(file_name):
        await message.answer("Поддерживаемые форматы: xlsx, xls, csv, txt, docx, pdf, jpg, png, webp")
        return
    if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
        await message.answer(f"Файл слишком большой. Максимум: {MAX_FILE_SIZE_BYTES // (1024*1024)} МБ")
        return

    status_msg = await message.answer("🔄 Анализирую файл...", parse_mode="Markdown")
    file_io = await bot.download(document.file_id)
    file_bytes = file_io.read()

    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))

    suffix = Path(file_name).suffix or ".xlsx"
    safe_name = sanitize_filename(document.file_name or "price")
    fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix=f"price_{safe_name}_", dir=str(_UPLOAD_ROOT))
    os.close(fd)
    Path(temp_path).write_bytes(file_bytes)

    parser = ExcelParserService(api_key=os.getenv("XAI_API_KEY"))
    try:
        mapping = await __import__(\'asyncio\').to_thread(parser.analyze_file_structure_sync, file_bytes, file_name)
        # Heuristic correction
        try:
            df_check = read_table(file_bytes, file_name, header=mapping.header_row_index, nrows=15)
            df_check.columns = [str(c).strip().replace("\n", " ") for c in df_check.columns]
            detected = detect_columns_by_keywords(df_check)
            cost_n = str(mapping.cost_price_col or "").strip().lower()
            bad_cost = (not mapping.cost_price_col or cost_n in ("ед", "ед.", "nan", "none") or cost_n.startswith("ед.") or any(x in cost_n for x in ("артикул", "sku", "единиц")))
            if bad_cost and detected.get("cost_price_col"):
                mapping.cost_price_col = detected["cost_price_col"]
            if (not mapping.product_name_col or str(mapping.product_name_col).lower() in ("nan", "none")) and detected.get("product_name_col"):
                mapping.product_name_col = detected["product_name_col"]
            if not mapping.quantity_col and detected.get("quantity_col"):
                mapping.quantity_col = detected["quantity_col"]
            if not mapping.cost_price_col or str(mapping.cost_price_col).strip().lower() in ("ед", "ед."):
                for c in df_check.columns:
                    cl = str(c).lower()
                    if any(x in cl for x in ("руб", "р.", "₽")) and "кол" not in cl:
                        mapping.cost_price_col = str(c)
                        break
        except Exception:
            pass

        await state.update_data(file_path=temp_path, file_name=file_name, mapping=mapping.model_dump())
        sell_info = f"\n• Колонка цены продажи: `{mapping.selling_price_col}`" if mapping.selling_price_col else "\n• Колонка цены продажи: не найдена (будет наценка 100%)"
        await status_msg.edit_text(
            f"✅ **Структура определена!**\n\n"
            f"• Строка шапки: `{mapping.header_row_index + 1}`\n"
            f"• Колонка товара: `{mapping.product_name_col}`\n"
            f"• Колонка себестоимости: `{mapping.cost_price_col}`{sell_info}",
            parse_mode="Markdown",
        )
        await prompt_mapping_confirm(message, state, mapping)
    except Exception as e:
        _cleanup_temp_file(temp_path)
        await status_msg.edit_text(f"❌ Ошибка при анализе файла")
        import logging
        logging.getLogger(__name__).exception("File analysis error")


async def prompt_mapping_confirm(message: Message, state: FSMContext, mapping):
    from services.marginator.file_io import read_table
    data = await state.get_data()
    cols = []
    try:
        file_bytes = await _load_file_bytes_from_state(data)
        if file_bytes:
            header_idx = getattr(mapping, "header_row_index", 0) if not isinstance(mapping, dict) else mapping.get("header_row_index", 0)
            df = read_table(file_bytes, data.get("file_name", "f.xlsx"), header=header_idx, nrows=3)
            cols = [str(c) for c in df.columns]
    except Exception:
        cols = []
    await state.update_data(file_columns=cols)

    if isinstance(mapping, dict):
        product = mapping.get("product_name_col")
        cost = mapping.get("cost_price_col")
        sell = mapping.get("selling_price_col")
        qty = mapping.get("quantity_col")
        header = mapping.get("header_row_index", 0)
        weight = mapping.get("weight_col")
    else:
        product = mapping.product_name_col
        cost = mapping.cost_price_col
        sell = mapping.selling_price_col
        qty = mapping.quantity_col
        header = mapping.header_row_index
        weight = getattr(mapping, "weight_col", None)

    await message.answer(
        f"Проверьте колонки:\n\n"
        f"• Шапка: строка {int(header) + 1}\n"
        f"• Товар: {product or \'—\'}\n"
        f"• Себестоимость: {cost or \'—\'}\n"
        f"• Цена продажи: {sell or \'—\'}\n"
        f"• Количество: {qty or \'—\'}\n"
        f"• Вес: {weight or \'—\'}\n\n"
        f"Если бот ошибся — «Изменить колонки».",
        reply_markup=get_mapping_confirm_keyboard(),
    )
    await state.set_state(CalcState.confirm_mapping)


@marginator_router.callback_query(CalcState.confirm_mapping, F.data == "map_ok")
async def mapping_ok(callback, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    raw = data.get("mapping") or {}
    try:
        mapping = TableMappingSchema.model_validate(raw) if isinstance(raw, dict) else raw
    except Exception:
        mapping = raw
    await prompt_price_column_or_params(callback.message, state, mapping)


@marginator_router.callback_query(CalcState.confirm_mapping, F.data == "map_edit")
@marginator_router.callback_query(CalcState.map_pick_column, F.data == "map_edit")
async def mapping_edit(callback, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Что исправить?", reply_markup=get_mapping_field_keyboard())
    await state.set_state(CalcState.map_pick_field)


@marginator_router.callback_query(CalcState.map_pick_field, F.data.startswith("map_field_"))
async def mapping_pick_field(callback, state: FSMContext):
    field = (callback.data or "").replace("map_field_", "")
    await state.update_data(map_edit_field=field)
    data = await state.get_data()
    cols = data.get("file_columns") or []
    if not cols:
        await callback.answer("Список колонок пуст", show_alert=True)
        return
    labels = {"product": "товар", "cost": "себестоимость", "sell": "цену продажи", "qty": "количество", "weight": "вес (кг)"}
    await callback.answer()
    await callback.message.edit_text(f"Выберите колонку для: {labels.get(field, field)}", reply_markup=get_column_pick_keyboard(cols))


@marginator_router.callback_query(CalcState.map_pick_column, F.data.startswith("map_col_"))
async def mapping_pick_col(callback, state: FSMContext):
    data = await state.get_data()
    cols = data.get("file_columns") or []
    try:
        idx = int((callback.data or "").rsplit("_", 1)[-1])
        col = cols[idx]
    except Exception:
        await callback.answer("Ошибка", show_alert=True)
        return
    field = data.get("map_edit_field")
    mapping = dict(data.get("mapping") or {})
    key_map = {"product": "product_name_col", "cost": "cost_price_col", "sell": "selling_price_col", "qty": "quantity_col", "weight": "weight_col"}
    mk = key_map.get(field)
    if mk:
        mapping[mk] = col
    await state.update_data(mapping=mapping)
    await callback.answer(f"→ {col}")
    try:
        mobj = TableMappingSchema.model_validate(mapping)
    except Exception:
        mobj = mapping
    await prompt_mapping_confirm(callback.message, state, mobj)


async def prompt_parameter_setup(message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")
    uid = message.from_user.id if message.from_user else None
    presets = []
    if uid:
        with SessionLocal() as db:
            from services.marginator.db_service import MarginatorDBService
            presets = MarginatorDBService.list_presets(db, uid)
            presets = [pr for pr in presets if (pr.calc_mode or "marketplace") == mode]
    kb = get_params_keyboard_with_presets(presets, mode=mode)
    text = "⚙️ Настройка B2B-параметров\nПресет, по умолчанию или вручную:" if mode == "b2b" else "⚙️ Финансовые параметры\nПресет, по умолчанию, шаблон МП или вручную."
    await message.answer(text, reply_markup=kb)
    await state.set_state(CalcState.input_commission)


async def prompt_price_column_or_params(message, state: FSMContext, mapping):
    from services.marginator.file_io import list_price_tier_columns, read_table
    data = await state.get_data()
    file_bytes = await _load_file_bytes_from_state(data)
    tiers = []
    if file_bytes:
        try:
            header_idx = getattr(mapping, "header_row_index", 0) if not isinstance(mapping, dict) else mapping.get("header_row_index", 0)
            df = read_table(file_bytes, data.get("file_name", "f.xlsx"), header=header_idx, nrows=5)
            df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]
            tiers = list_price_tier_columns(df)
        except Exception:
            tiers = []
    await state.update_data(price_tier_columns=tiers)
    if len(tiers) >= 2:
        await message.answer("Найдено несколько колонок с ценой. Выберите, от какой считать закупку:", reply_markup=get_price_col_keyboard(tiers))
        await state.set_state(CalcState.select_price_col)
    else:
        await prompt_parameter_setup(message, state)


@marginator_router.callback_query(CalcState.select_price_col, F.data.startswith("price_col_"))
async def select_price_col(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tiers = data.get("price_tier_columns") or []
    raw = callback.data or ""
    if raw == "price_col_auto":
        await callback.answer("Авто")
    else:
        try:
            idx = int(raw.replace("price_col_", ""))
            chosen = tiers[idx]
            mapping = dict(data.get("mapping") or {})
            mapping["cost_price_col"] = chosen
            await state.update_data(mapping=mapping)
            await callback.answer(f"→ {chosen}")
        except Exception:
            await callback.answer("Ошибка", show_alert=True)
            return
    await prompt_parameter_setup(callback.message, state)


@marginator_router.callback_query(CalcState.select_price_col, F.data == "cancel_flow")
async def cancel_flow_price_col(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("❌ Отменено. Отправьте новый файл или нажмите «🔄 Новый расчёт».", reply_markup=get_main_reply_keyboard())
    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))
    await state.clear()
