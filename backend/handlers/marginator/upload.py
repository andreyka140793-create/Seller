"""File upload and mapping handlers."""
import os
import tempfile
from pathlib import Path

import pandas as pd
from aiogram import F, Bot
from aiogram.types import Message, CallbackQuery
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


async def _save_document_to_disk(bot: Bot, document, file_name: str) -> tuple[str, bytes]:
    """Скачать документ Telegram во временный файл. Возвращает (path, bytes)."""
    file_io = await bot.download(document.file_id)
    file_bytes = file_io.read()
    suffix = Path(file_name).suffix or ".xlsx"
    safe_name = sanitize_filename(document.file_name or "price")
    fd, temp_path = tempfile.mkstemp(
        suffix=suffix, prefix=f"price_{safe_name}_", dir=str(_UPLOAD_ROOT)
    )
    os.close(fd)
    Path(temp_path).write_bytes(file_bytes)
    return temp_path, file_bytes




async def fail_current_keep_queue(state: FSMContext, *, cleanup_path: bool = True) -> int:
    """Сбросить текущий файл, но сохранить очередь. Возвращает длину очереди."""
    data = await state.get_data()
    if cleanup_path:
        _cleanup_temp_file(data.get("file_path"))
    queue = list(data.get("file_queue") or [])
    mode = data.get("calc_mode") or "marketplace"
    # не трогаем queue; чистим только «текущий» контекст
    await state.update_data(
        file_path=None,
        file_name=None,
        mapping=None,
        upload_busy=False,
        calc_mode=mode,
        file_queue=queue,
        # параметры прошлого файла не нужны
        commission_percent=None,
        logistics_cost=None,
        packaging_cost=None,
        tax_rate_percent=None,
        target_margin_percent=None,
        fx_rate=None,
        logistics_per_kg=None,
    )
    return len(queue)


def queue_continue_keyboard(queue_len: int, *, after_success: bool = False):
    """Клавиатура очереди. after_success=True — после удачного расчёта (те же параметры)."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    if queue_len > 0:
        if after_success:
            rows.append([InlineKeyboardButton(
                text=f"⚡ Следующий с теми же параметрами ({queue_len})",
                callback_data="queue_next_same",
            )])
            rows.append([InlineKeyboardButton(
                text=f"🚀 Всю очередь с теми же параметрами",
                callback_data="queue_all_same",
            )])
            rows.append([InlineKeyboardButton(
                text="➡️ Следующий (заново настроить)",
                callback_data="queue_next",
            )])
        else:
            rows.append([InlineKeyboardButton(
                text=f"➡️ Следующий из очереди ({queue_len})",
                callback_data="queue_next",
            )])
    rows.append([InlineKeyboardButton(
        text="🗑 Очистить очередь и выйти",
        callback_data="queue_clear",
    )])
    rows.append([InlineKeyboardButton(
        text="🔄 Новый расчёт",
        callback_data="new_calc",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Параметры, которые переносим на следующий файл очереди
_QUEUE_PARAM_KEYS = (
    "calc_mode",
    "commission_percent",
    "logistics_cost",
    "packaging_cost",
    "tax_rate_percent",
    "tax_mode",
    "freight_cost",
    "manager_bonus_percent",
    "is_vat_included",
    "target_margin_percent",
    "fx_rate",
    "fx_code",
    "logistics_per_kg",
)


def _snapshot_params(data: dict) -> dict:
    return {k: data.get(k) for k in _QUEUE_PARAM_KEYS if data.get(k) is not None}

async def _enqueue_file(state: FSMContext, path: str, file_name: str) -> int:
    data = await state.get_data()
    queue = list(data.get("file_queue") or [])
    queue.append({"path": path, "name": file_name})
    await state.update_data(file_queue=queue)
    return len(queue)


async def process_price_file(
    message: Message,
    state: FSMContext,
    *,
    file_bytes: bytes,
    file_name: str,
    temp_path: str,
    status_msg: Message | None = None,
):
    """Полный анализ одного прайса (уже сохранённого на диск)."""
    if status_msg is None:
        status_msg = await message.answer("🔄 Анализирую файл...")
    parser = ExcelParserService(api_key=os.getenv("XAI_API_KEY"))
    try:
        mapping = await __import__("asyncio").to_thread(
            parser.analyze_file_structure_sync, file_bytes, file_name
        )
        try:
            df_check = read_table(file_bytes, file_name, header=mapping.header_row_index, nrows=15)
            df_check.columns = [str(c).strip().replace("\n", " ") for c in df_check.columns]
            detected = detect_columns_by_keywords(df_check)
            cost_n = str(mapping.cost_price_col or "").strip().lower()
            bad_cost = (
                not mapping.cost_price_col
                or cost_n in ("ед", "ед.", "nan", "none")
                or cost_n.startswith("ед.")
                or any(x in cost_n for x in ("артикул", "sku", "единиц"))
            )
            if bad_cost and detected.get("cost_price_col"):
                mapping.cost_price_col = detected["cost_price_col"]
            if (
                not mapping.product_name_col
                or str(mapping.product_name_col).lower() in ("nan", "none")
            ) and detected.get("product_name_col"):
                mapping.product_name_col = detected["product_name_col"]
            if not mapping.quantity_col and detected.get("quantity_col"):
                mapping.quantity_col = detected["quantity_col"]
        except Exception:
            pass

        data = await state.get_data()
        queue = data.get("file_queue") or []
        queue_note = f"\n\n📋 В очереди ещё файлов: {len(queue)}" if queue else ""

        await state.update_data(
            file_path=temp_path,
            file_name=file_name,
            mapping=mapping.model_dump() if hasattr(mapping, "model_dump") else mapping,
            upload_busy=True,
        )
        sell_info = (
            f"\n• Колонка цены продажи: `{mapping.selling_price_col}`"
            if mapping.selling_price_col
            else ""
        )
        await status_msg.edit_text(
            f"✅ Шаг 2/3 — колонки (`{file_name}`)\n\n"
            f"• Строка шапки: `{mapping.header_row_index + 1}`\n"
            f"• Колонка товара: `{mapping.product_name_col}`\n"
            f"• Колонка себестоимости: `{mapping.cost_price_col}`{sell_info}"
            f"{queue_note}",
            parse_mode="Markdown",
        )
        await prompt_mapping_confirm(message, state, mapping)
    except Exception as e:
        _cleanup_temp_file(temp_path)
        qlen = await fail_current_keep_queue(state, cleanup_path=False)
        from services.marginator.document_loader import friendly_load_error
        err_text = "❌ " + friendly_load_error(e, file_name)
        try:
            await status_msg.edit_text(err_text[:500], parse_mode="Markdown")
        except Exception:
            await message.answer(err_text[:500])
        import logging
        logging.getLogger(__name__).exception("File analysis error")
        if qlen > 0:
            await message.answer(
                f"Очередь не сброшена: ещё {qlen} файл(ов).\n"
                "Можно взять следующий или очистить очередь.",
                reply_markup=queue_continue_keyboard(qlen),
            )
        else:
            await message.answer(
                "Очередь пуста. Отправьте другой файл или «🔄 Новый расчёт».",
                reply_markup=get_main_reply_keyboard(),
            )


async def start_next_queued_file(message: Message, state: FSMContext) -> bool:
    """Взять следующий файл из очереди и запустить анализ. True если был файл."""
    data = await state.get_data()
    queue = list(data.get("file_queue") or [])
    if not queue:
        await state.update_data(upload_busy=False)
        return False
    item = queue.pop(0)
    mode = data.get("calc_mode") or "marketplace"
    await state.update_data(file_queue=queue, upload_busy=True, calc_mode=mode)
    await state.set_state(CalcState.upload_file)
    path = item.get("path")
    name = item.get("name") or "price.xlsx"
    if not path or not Path(path).is_file():
        await message.answer(f"⚠️ Файл из очереди недоступен: {name}")
        qlen = len(queue)
        if qlen:
            await message.answer(
                "Попробуйте следующий.",
                reply_markup=queue_continue_keyboard(qlen),
            )
        return False  # НЕ авто-каскад при битых путях — пользователь жмёт кнопку
    await message.answer(
        f"➡️ Из очереди ({len(queue)} ещё ждут):\n`{name}`",
        parse_mode="Markdown",
    )
    file_bytes = Path(path).read_bytes()
    await process_price_file(
        message, state, file_bytes=file_bytes, file_name=name, temp_path=path
    )
    return True


@marginator_router.message(CalcState.upload_file, F.document)
async def handle_file_upload(message: Message, state: FSMContext, bot: Bot):
    document = message.document
    file_name = (document.file_name or "price.xlsx").lower()

    from services.marginator.document_loader import is_supported
    if not is_supported(file_name):
        from services.marginator.document_loader import supported_formats_hint
        await message.answer(f"Поддерживаемые форматы: {supported_formats_hint()}")
        return
    if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
        await message.answer(f"Файл слишком большой. Максимум: {MAX_FILE_SIZE_BYTES // (1024*1024)} МБ")
        return

    data = await state.get_data()
    # Уже идёт разбор / настройка другого файла → в очередь, без параллельного анализа
    if data.get("upload_busy") or data.get("file_path"):
        temp_path, _ = await _save_document_to_disk(bot, document, file_name)
        n = await _enqueue_file(state, temp_path, file_name)
        await message.answer(
            f"📥 `{document.file_name or file_name}` добавлен в очередь (№{n}).\n"
            "Сейчас работаем с другим файлом — этот посчитаем следом, "
            "когда закончите текущий расчёт (или нажмёте «Новый расчёт» / «Отмена»).",
            parse_mode="Markdown",
        )
        return

    await state.update_data(upload_busy=True)
    status_msg = await message.answer("🔄 Анализирую файл...", parse_mode="Markdown")
    temp_path, file_bytes = await _save_document_to_disk(bot, document, file_name)
    await process_price_file(
        message, state,
        file_bytes=file_bytes,
        file_name=file_name,
        temp_path=temp_path,
        status_msg=status_msg,
    )



@marginator_router.message(F.document)
async def handle_file_any_state(message: Message, state: FSMContext, bot: Bot):
    """Если прислали файл не в upload_file — либо очередь, либо старт нового."""
    current = await state.get_state()
    # уже обрабатывается upload_file handler
    if current == CalcState.upload_file.state:
        return
    # compare mode has own handlers
    if current in (
        CalcState.compare_upload_a.state,
        CalcState.compare_upload_b.state,
    ):
        return

    document = message.document
    file_name = (document.file_name or "price.xlsx").lower()
    from services.marginator.document_loader import is_supported
    if not is_supported(file_name):
        return  # ignore non-price docs

    data = await state.get_data()
    if data.get("upload_busy") or data.get("file_path") or (
        current and current != CalcState.select_mode.state
    ):
        if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
            await message.answer("Файл слишком большой.")
            return
        temp_path, _ = await _save_document_to_disk(bot, document, file_name)
        n = await _enqueue_file(state, temp_path, file_name)
        await message.answer(
            f"📥 `{document.file_name or file_name}` в очереди (№{n}).\n"
            "Завершите текущий расчёт — затем бот возьмёт этот файл. "
            "Или «❌ Отмена» / «🔄 Новый расчёт».",
            parse_mode="Markdown",
        )
        return

    # свободны — начинаем как новый расчёт
    if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
        await message.answer("Файл слишком большой.")
        return
    await state.set_state(CalcState.upload_file)
    await state.update_data(upload_busy=True, calc_mode=data.get("calc_mode") or "marketplace")
    status_msg = await message.answer("🔄 Анализирую файл...")
    temp_path, file_bytes = await _save_document_to_disk(bot, document, file_name)
    await process_price_file(
        message, state,
        file_bytes=file_bytes,
        file_name=file_name,
        temp_path=temp_path,
        status_msg=status_msg,
    )


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
        f"• Товар: {product or '—'}\n"
        f"• Себестоимость: {cost or '—'}\n"
        f"• Цена продажи: {sell or '—'}\n"
        f"• Количество: {qty or '—'}\n"
        f"• Вес: {weight or '—'}\n\n"
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
    data0 = await state.get_data()
    if data0.get("reuse_params"):
        await state.update_data(reuse_params=False)
        from handlers.marginator.params import show_params_summary
        await message.answer(
            "Параметры прошлого расчёта уже подставлены. "
            "Проверьте сводку и нажмите «Рассчитать»."
        )
        await show_params_summary(message, state)
        return
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




async def process_queue_item_same_params(
    message: Message,
    state: FSMContext,
    *,
    params: dict,
) -> bool:
    """Один файл из очереди: авто-маппинг + те же параметры + сразу расчёт."""
    data = await state.get_data()
    queue = list(data.get("file_queue") or [])
    if not queue:
        return False

    item = queue.pop(0)
    path = item.get("path")
    name = item.get("name") or "price.xlsx"
    await state.update_data(file_queue=queue)

    if not path or not Path(path).is_file():
        await message.answer(f"⚠️ Пропуск: файл недоступен `{name}`", parse_mode="Markdown")
        return True

    await message.answer(f"⚡ Очередь → `{name}` (те же параметры)…", parse_mode="Markdown")
    file_bytes = Path(path).read_bytes()

    try:
        parser = ExcelParserService(api_key=os.getenv("XAI_API_KEY"))
        mapping = await __import__("asyncio").to_thread(
            parser.analyze_file_structure_sync, file_bytes, name
        )
        try:
            df_check = read_table(file_bytes, name, header=mapping.header_row_index, nrows=15)
            df_check.columns = [str(c).strip().replace("\n", " ") for c in df_check.columns]
            detected = detect_columns_by_keywords(df_check)
            if detected.get("cost_price_col") and (
                not mapping.cost_price_col
                or str(mapping.cost_price_col).lower() in ("ед", "ед.", "nan")
            ):
                mapping.cost_price_col = detected["cost_price_col"]
            if detected.get("product_name_col") and not mapping.product_name_col:
                mapping.product_name_col = detected["product_name_col"]
        except Exception:
            pass

        await state.update_data(
            file_path=path,
            file_name=name,
            mapping=mapping.model_dump() if hasattr(mapping, "model_dump") else mapping,
            upload_busy=True,
            **params,
        )
        await state.set_state(CalcState.confirm_params)

        from handlers.marginator.calculation import execute_calculation_core
        data2 = await state.get_data()
        user_id = message.from_user.id if message.from_user else 0
        await execute_calculation_core(message, state, data2, file_bytes, user_id)
        return True
    except Exception as e:
        _cleanup_temp_file(path)
        import logging
        logging.getLogger(__name__).exception("queue same-params failed")
        qlen = len((await state.get_data()).get("file_queue") or [])
        await message.answer(
            f"❌ `{name}`: {type(e).__name__}: {e}",
            parse_mode="Markdown",
            reply_markup=queue_continue_keyboard(qlen, after_success=False),
        )
        await state.update_data(upload_busy=False)
        return False


@marginator_router.callback_query(F.data == "queue_next_same")
async def cb_queue_next_same(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    params = _snapshot_params(data)
    if data.get("file_path"):
        _cleanup_temp_file(data.get("file_path"))
    await state.update_data(file_path=None, mapping=None)
    ok = await process_queue_item_same_params(callback.message, state, params=params)
    left = (await state.get_data()).get("file_queue") or []
    if not ok and not left:
        await callback.message.answer(
            "Очередь пуста.",
            reply_markup=get_main_reply_keyboard(),
        )


@marginator_router.callback_query(F.data == "queue_all_same")
async def cb_queue_all_same(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Считаю всю очередь…")
    data = await state.get_data()
    params = _snapshot_params(data)
    if data.get("file_path"):
        _cleanup_temp_file(data.get("file_path"))
    await state.update_data(file_path=None, mapping=None)

    processed = 0
    while True:
        data = await state.get_data()
        if not data.get("file_queue"):
            break
        ok = await process_queue_item_same_params(callback.message, state, params=params)
        if not ok:
            break
        processed += 1
        if processed >= 20:
            await callback.message.answer("Остановка: лимит 20 файлов за раз.")
            break

    data = await state.get_data()
    left = len(data.get("file_queue") or [])
    if left:
        await callback.message.answer(
            f"Готово частично. Осталось в очереди: {left}",
            reply_markup=queue_continue_keyboard(left, after_success=True),
        )
    else:
        await callback.message.answer(
            f"✅ Очередь обработана ({processed} файл(ов)).",
            reply_markup=get_main_reply_keyboard(),
        )


@marginator_router.callback_query(F.data == "queue_next")
async def cb_queue_next(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    # освободить текущий temp, если есть
    if data.get("file_path"):
        _cleanup_temp_file(data.get("file_path"))
    await state.update_data(
        file_path=None,
        mapping=None,
        upload_busy=False,
    )
    from handlers.marginator.upload import start_next_queued_file
    ok = await start_next_queued_file(callback.message, state)
    if not ok:
        await callback.message.answer(
            "Очередь пуста. Отправьте новый прайс или «🔄 Новый расчёт».",
            reply_markup=get_main_reply_keyboard(),
        )


@marginator_router.callback_query(F.data == "same_params_new")
async def cb_same_params_new_file(callback: CallbackQuery, state: FSMContext):
    """Новый прайс, но комиссия/лог/налог/курс/порог риска те же."""
    await callback.answer()
    data = await state.get_data()
    # чистим только файл, параметры оставляем
    if data.get("file_path"):
        _cleanup_temp_file(data.get("file_path"))
    keep = {
        k: data.get(k)
        for k in (
            "calc_mode",
            "commission_percent",
            "logistics_cost",
            "packaging_cost",
            "tax_rate_percent",
            "tax_mode",
            "freight_cost",
            "manager_bonus_percent",
            "is_vat_included",
            "vat_rate_percent",
            "target_margin_percent",
            "fx_rate",
            "fx_code",
            "logistics_per_kg",
            "risk_threshold_percent",
        )
        if data.get(k) is not None
    }
    # не затираем очередь целиком — пользователь может слать новый файл
    queue = data.get("file_queue") or []
    await state.set_state(CalcState.upload_file)
    await state.update_data(
        file_path=None,
        file_name=None,
        mapping=None,
        upload_busy=False,
        file_queue=queue,
        reuse_params=True,
        **keep,
    )
    mode = keep.get("calc_mode", "marketplace")
    await callback.message.answer(
        "📎 *Новый файл с теми же параметрами*\n\n"
        f"Режим: {mode}\n"
        "Параметры (комиссия, логистика, налог, курс, порог риска) сохранены.\n\n"
        "Пришлите следующий прайс — сразу к проверке колонок и расчёту.",
        parse_mode="Markdown",
        reply_markup=get_main_reply_keyboard(),
    )
