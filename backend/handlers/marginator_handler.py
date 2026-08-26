import os
import tempfile
from pathlib import Path

import pandas as pd
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext

from states.marginator_states import CalcState
from keyboards.marginator_keyboards import (
    get_mode_keyboard,
    get_params_setup_keyboard,
    get_b2b_params_keyboard,
    get_skip_keyboard,
    get_history_keyboard,
    get_webapp_keyboard,
    get_run_keyboard,
    get_main_reply_keyboard,
    get_after_report_keyboard,
    get_params_keyboard_with_presets,
    get_price_col_keyboard,
    get_target_margin_keyboard,
    get_whatif_keyboard,
    MP_TEMPLATES,
)
from database import SessionLocal
from services.marginator.parser import ExcelParserService
from services.marginator.calculators import (
    MarketplaceCalculator,
    MarketplaceParams,
    B2BCalculator,
    B2BParams,
    BaseItem,
    min_selling_price_for_margin,
)
from services.marginator.exporter import ExcelExporterService
from services.marginator.db_service import MarginatorDBService
from services.marginator.analytics import AnalyticsService
from services.marginator.utils import clean_numeric_value
from services.marginator.schemas import TableMappingSchema
from services.marginator.file_io import resolve_column, detect_columns_by_keywords
from config import PurchasingConfig

marginator_router = Router()

# Лимит Telegram Bot API на скачивание файла ботом (~20 МБ)
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024

# Каталог для временных прайс-листов (на Amvera — /data/uploads)
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
    """Читает файл из временного пути в FSM (не hex)."""
    path = data.get("file_path")
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    return p.read_bytes()


# --- Команда /start ---

@marginator_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Добро пожаловать в Маржинатор!\n\n"
        "Считаю маржу, маржинальность, наценку, чистую прибыль и ROI "
        "(маркетплейс / B2B).\n"
        "Подробнее — «📖 Помощь».\n\n"
        "Выберите режим или «🔄 Новый расчёт».",
        reply_markup=get_main_reply_keyboard(),
    )
    await message.answer("Режим расчёта:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)



@marginator_router.message(Command("help"))
async def cmd_help(message: Message):
    max_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
    text = (
        "Справка Маржинатор\n\n"
        "Показатели:\n"
        "• Маржа, руб — выручка минус переменные (закуп, комиссия, логистика, упаковка)\n"
        "• Маржинальность % — маржа / выручка x 100\n"
        "• Наценка % — маржа / переменные x 100 (может быть больше 100%)\n"
        "• Чистая прибыль, руб — маржа минус налог\n"
        "• Рентабельность чистая % — чистая прибыль / выручка\n"
        "• ROI % — чистая прибыль / вложения в товар\n\n"
        "Форматы: xlsx, xls, csv, txt, docx, pdf, jpg/png\n"
        f"Макс. размер: {max_mb} МБ\n\n"
        "Кнопки внизу: Новый расчёт, История, Помощь, Отмена\n\n"
        "Шаблоны WB≈/Ozon≈/ЯМ≈ — только ориентиры, не тарифы МП.\n"
        "После отчёта: «Комиссия ±2%» — быстрый пересчёт."
    )
    await message.answer(text)


# ---

# --- Выбор режима ---

@marginator_router.callback_query(CalcState.select_mode, F.data.in_({"mode_marketplace", "mode_b2b"}))
async def select_calc_mode(callback: CallbackQuery, state: FSMContext):
    mode = "marketplace" if callback.data == "mode_marketplace" else "b2b"
    await state.update_data(calc_mode=mode)
    label = "Маркетплейс" if mode == "marketplace" else "B2B"
    await callback.message.edit_text(
        f"✅ Режим: **{label}**\n\n"
        "Теперь отправьте прайс: `.xlsx` / `.xls` / `.csv` / `.txt` / `.docx` / `.pdf` / фото.",
        parse_mode="Markdown",
    )
    await state.set_state(CalcState.upload_file)
    await callback.answer()


# --- Загрузка и анализ файла ---

@marginator_router.message(CalcState.upload_file, F.document)
async def handle_excel_upload(message: Message, state: FSMContext, bot: Bot):
    document = message.document
    file_name = (document.file_name or "price.xlsx").lower()

    from services.marginator.document_loader import is_supported
    if not is_supported(file_name):
        await message.answer(
            "Поддерживаемые форматы:\n"
            "• Excel: `.xlsx`, `.xls`\n"
            "• Таблицы: `.csv`, `.tsv`\n"
            "• Текст: `.txt`\n"
            "• Word: `.docx`\n"
            "• PDF: `.pdf` (с текстовым слоем)\n"
            "• Фото/скрин: `.jpg`, `.png`, `.webp`"
        )
        return

    if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
        await message.answer(
            f"❌ Файл слишком большой (**{document.file_size / (1024 * 1024):.1f} МБ**).\n"
            f"Максимум: **{MAX_FILE_SIZE_BYTES // (1024 * 1024)} МБ**."
        )
        return

    status_msg = await message.answer(
        "🔄 **Анализирую файл (Grok + эвристика)...**", parse_mode="Markdown"
    )

    file_io = await bot.download(document.file_id)
    file_bytes = file_io.read()

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        await status_msg.edit_text(
            f"❌ Файл превышает лимит {MAX_FILE_SIZE_BYTES // (1024 * 1024)} МБ."
        )
        return

    # Сохраняем на диск вместо hex в FSM (экономия памяти)
    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))

    suffix = Path(file_name).suffix or ".xlsx"
    fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="price_", dir=str(_UPLOAD_ROOT))
    os.close(fd)
    Path(temp_path).write_bytes(file_bytes)

    parser = ExcelParserService(api_key=os.getenv("XAI_API_KEY"))

    try:
        mapping = await parser.analyze_file_structure(file_bytes, file_name)

        # Жёсткая коррекция: не оставляем «Ед.» / артикул как цену
        from services.marginator.file_io import read_table, detect_columns_by_keywords
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
            if (not mapping.product_name_col or str(mapping.product_name_col).lower() in ("nan", "none")) and detected.get("product_name_col"):
                mapping.product_name_col = detected["product_name_col"]
            if not mapping.quantity_col and detected.get("quantity_col"):
                mapping.quantity_col = detected["quantity_col"]
            # запасной вариант: любая колонка с «руб» в названии
            if not mapping.cost_price_col or str(mapping.cost_price_col).strip().lower() in ("ед", "ед."):
                for c in df_check.columns:
                    cl = str(c).lower()
                    if any(x in cl for x in ("руб", "р.", "₽")) and "кол" not in cl:
                        mapping.cost_price_col = str(c)
                        break
        except Exception:
            pass

        await state.update_data(
            file_path=temp_path,
            file_name=file_name,
            mapping=mapping.model_dump(),
        )

        sell_info = (
            f"\n• Колонка цены продажи: `{mapping.selling_price_col}`"
            if mapping.selling_price_col
            else "\n• Колонка цены продажи: не найдена (будет наценка 100%)"
        )
        await status_msg.edit_text(
            f"✅ **Структура определена!**\n\n"
            f"• Строка шапки: `{mapping.header_row_index + 1}`\n"
            f"• Колонка товара: `{mapping.product_name_col}`\n"
            f"• Колонка себестоимости: `{mapping.cost_price_col}`"
            f"{sell_info}",
            parse_mode="Markdown",
        )

        await prompt_price_column_or_params(message, state, mapping)

    except Exception as e:
        _cleanup_temp_file(temp_path)
        await status_msg.edit_text(f"❌ Ошибка при анализе файла: `{str(e)}`", parse_mode="Markdown")


async def prompt_parameter_setup(message: Message, state: FSMContext):
    """Параметры: пресеты пользователя + default + ручной ввод."""
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")
    uid = message.from_user.id if message.from_user else None
    presets = []
    if uid:
        with SessionLocal() as db:
            presets = MarginatorDBService.list_presets(db, uid)
            # filter by mode
            presets = [pr for pr in presets if (pr.calc_mode or "marketplace") == mode]

    kb = get_params_keyboard_with_presets(presets, mode=mode)
    if mode == "b2b":
        await message.answer(
            "⚙️ Настройка B2B-параметров\n"
            "Пресет, по умолчанию или вручную:",
            reply_markup=kb,
        )
    else:
        await message.answer(
            "⚙️ Финансовые параметры\n"
            "Пресет, по умолчанию, шаблон МП или вручную.\n\n"
            "⚠️ Кнопки WB≈ / Ozon≈ / ЯМ≈ — примерные ориентиры, "
            "не актуальные тарифы площадок. Точные % — из вашего кабинета.",
            reply_markup=kb,
        )
    await state.set_state(CalcState.input_commission)


async def prompt_price_column_or_params(message: Message, state: FSMContext, mapping):
    """Если несколько ценовых колонок — спросить, иначе параметры."""
    from services.marginator.file_io import list_price_tier_columns, read_table
    data = await state.get_data()
    file_bytes = await _load_file_bytes_from_state(data)
    tiers = []
    if file_bytes:
        try:
            df = read_table(file_bytes, data.get("file_name", "f.xlsx"), header=mapping.header_row_index, nrows=5)
            df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]
            tiers = list_price_tier_columns(df)
        except Exception:
            tiers = []
    await state.update_data(price_tier_columns=tiers)
    if len(tiers) >= 2:
        await message.answer(
            "Найдено несколько колонок с ценой (ступени опта).\n"
            "Выберите, от какой считать закупку:",
            reply_markup=get_price_col_keyboard(tiers),
        )
        await state.set_state(CalcState.select_price_col)
    else:
        await prompt_parameter_setup(message, state)


async def prompt_target_margin(message: Message, state: FSMContext):
    await message.answer(
        "🎯 Целевая маржинальность %\n"
        "Посчитаю мин. цену продажи для каждой позиции.\n"
        "Или пропустите шаг.",
        reply_markup=get_target_margin_keyboard(),
    )
    await state.set_state(CalcState.input_target_margin)


# --- Быстрый выбор по умолчанию ---

@marginator_router.callback_query(CalcState.input_commission, F.data == "params_default")
async def apply_default_params(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")

    if mode == "b2b":
        await state.update_data(
            freight_cost=0.0,
            manager_bonus_percent=0.0,
            is_vat_included=True,
        )
        await callback.message.edit_text(
            "✅ **B2B-параметры установлены:**\n"
            "• Фрахт: `0 ₽/ед.`\n"
            "• Бонус менеджера: `0%`\n"
            "• НДС 20%: учтён\n\n"
            "Параметры сохранены.",
            parse_mode="Markdown",
        )
    else:
        await state.update_data(
            commission_percent=PurchasingConfig.DEFAULT_MP_COMMISSION_PCT,
            logistics_cost=PurchasingConfig.DEFAULT_LOGISTICS_RUB,
            packaging_cost=PurchasingConfig.DEFAULT_PACKAGING_RUB,
            tax_rate_percent=PurchasingConfig.DEFAULT_TAX_PCT,
        )
        await callback.message.edit_text(
            f"✅ Параметры: комиссия {PurchasingConfig.DEFAULT_MP_COMMISSION_PCT}%, "
            f"лог. {PurchasingConfig.DEFAULT_LOGISTICS_RUB}₽, "
            f"уп. {PurchasingConfig.DEFAULT_PACKAGING_RUB}₽, "
            f"налог {PurchasingConfig.DEFAULT_TAX_PCT}%",
            parse_mode="Markdown",
        )

    await callback.answer()
    await prompt_target_margin(callback.message, state)


# --- Ручной пошаговый ввод (маркетплейс) ---

@marginator_router.callback_query(F.data == "params_custom")
async def start_custom_params(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")

    if mode == "b2b":
        await callback.message.edit_text(
            "1️⃣ **Введите фрахт / доставку на единицу в ₽** (например, `500`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_freight)
    else:
        await callback.message.edit_text(
            "1️⃣ **Введите комиссию маркетплейса в %** (например, `12.5`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
    await callback.answer()


@marginator_router.message(CalcState.input_commission)
async def process_commission_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(",", "."))
        if not (0 <= val <= 100):
            raise ValueError
        await state.update_data(commission_percent=val)
        await message.answer(
            "2️⃣ **Введите среднюю логистику на единицу в ₽** (например, `80`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_logistics)
    except (ValueError, AttributeError):
        await message.answer("❌ Введите корректное число от 0 до 100.")


@marginator_router.message(CalcState.input_logistics)
async def process_logistics_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(",", "."))
        if val < 0:
            raise ValueError
        await state.update_data(logistics_cost=val)
        await message.answer(
            "3️⃣ **Введите стоимость упаковки на единицу в ₽** (например, `30`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_packaging)
    except (ValueError, AttributeError):
        await message.answer("❌ Введите неотрицательное число.")


@marginator_router.message(CalcState.input_packaging)
async def process_packaging_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(",", "."))
        if val < 0:
            raise ValueError
        await state.update_data(packaging_cost=val)
        await message.answer(
            "4️⃣ **Введите ставку налога в %** (например, `6` для УСН 6% или `15` для УСН 15%):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_tax)
    except (ValueError, AttributeError):
        await message.answer("❌ Введите неотрицательное число.")


@marginator_router.message(CalcState.input_tax)
async def process_tax_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(",", "."))
        if not (0 <= val <= 100):
            raise ValueError
        await state.update_data(tax_rate_percent=val)

        data = await state.get_data()
        await message.answer(
            f"✅ **Все параметры сохранены!**\n\n"
            f"• Комиссия: `{data.get('commission_percent')}%`\n"
            f"• Логистика: `{data.get('logistics_cost')} ₽`\n"
            f"• Упаковка: `{data.get('packaging_cost')} ₽`\n"
            f"• Налог: `{data.get('tax_rate_percent')}%`\n\n"
            "Параметры приняты.",
            parse_mode="Markdown",
        )
        await prompt_target_margin(message, state)
    except (ValueError, AttributeError):
        await message.answer("❌ Введите корректную процентную ставку.")


# --- Ручной ввод B2B ---

@marginator_router.message(CalcState.input_freight)
async def process_freight_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(",", "."))
        if val < 0:
            raise ValueError
        await state.update_data(freight_cost=val)
        await message.answer(
            "2️⃣ **Введите бонус менеджера в %** (например, `2`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_manager_bonus)
    except (ValueError, AttributeError):
        await message.answer("❌ Введите неотрицательное число.")


@marginator_router.message(CalcState.input_manager_bonus)
async def process_manager_bonus_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(",", "."))
        if not (0 <= val <= 100):
            raise ValueError
        await state.update_data(manager_bonus_percent=val, is_vat_included=True)
        data = await state.get_data()
        await message.answer(
            f"✅ **B2B-параметры сохранены!**\n\n"
            f"• Фрахт: `{data.get('freight_cost')} ₽`\n"
            f"• Бонус менеджера: `{data.get('manager_bonus_percent')}%`\n"
            f"• НДС 20%: учтён\n\n"
            "Параметры приняты.",
            parse_mode="Markdown",
        )
        await prompt_target_margin(message, state)
    except (ValueError, AttributeError):
        await message.answer("❌ Введите число от 0 до 100.")


# --- Кнопка «Пропустить» ---

@marginator_router.callback_query(F.data == "skip_param")
async def skip_optional_param(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state == CalcState.input_commission.state:
        await state.update_data(commission_percent=0.0)
        await callback.message.edit_text(
            "2️⃣ **Введите среднюю логистику на единицу в ₽** (например, `80`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_logistics)

    elif current_state == CalcState.input_logistics.state:
        await state.update_data(logistics_cost=0.0)
        await callback.message.edit_text(
            "3️⃣ **Введите стоимость упаковки на единицу в ₽** (например, `30`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_packaging)

    elif current_state == CalcState.input_packaging.state:
        await state.update_data(packaging_cost=0.0)
        await callback.message.edit_text(
            "4️⃣ **Введите ставку налога в %** (например, `6`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_tax)

    elif current_state == CalcState.input_tax.state:
        await state.update_data(tax_rate_percent=0.0)
        data = await state.get_data()
        await callback.message.edit_text(
            f"✅ **Все параметры сохранены!**\n\n"
            f"• Комиссия: `{data.get('commission_percent', 0.0)}%`\n"
            f"• Логистика: `{data.get('logistics_cost', 0.0)} ₽`\n"
            f"• Упаковка: `{data.get('packaging_cost', 0.0)} ₽`\n"
            f"• Налог: `0%`\n\n"
            "Параметры приняты.",
            parse_mode="Markdown",
        )
        await callback.answer()
        await prompt_target_margin(callback.message, state)
        return

    elif current_state == CalcState.input_freight.state:
        await state.update_data(freight_cost=0.0)
        await callback.message.edit_text(
            "2️⃣ **Введите бонус менеджера в %** (например, `2`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.input_manager_bonus)

    elif current_state == CalcState.input_manager_bonus.state:
        await state.update_data(manager_bonus_percent=0.0, is_vat_included=True)
        data = await state.get_data()
        await callback.message.edit_text(
            f"✅ **B2B-параметры сохранены!**\n\n"
            f"• Фрахт: `{data.get('freight_cost', 0.0)} ₽`\n"
            f"• Бонус менеджера: `0%`\n"
            f"• НДС 20%: учтён\n\n"
            "Параметры приняты.",
            parse_mode="Markdown",
        )
        await callback.answer()
        await prompt_target_margin(callback.message, state)
        return

    await callback.answer()





# --- Выбор колонки цены (ступени B2B) ---

@marginator_router.callback_query(CalcState.select_price_col, F.data.startswith("price_col_"))
async def on_price_col_chosen(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tiers = data.get("price_tier_columns") or []
    raw = callback.data or ""
    if raw == "price_col_auto":
        await callback.answer("Оставляем как определил бот")
        await callback.message.edit_text("Колонка цены: как определил бот.")
        await prompt_parameter_setup(callback.message, state)
        return
    try:
        idx = int(raw.split("_")[-1])
        col = tiers[idx]
    except Exception:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    mapping = data.get("mapping") or {}
    if isinstance(mapping, dict):
        mapping["cost_price_col"] = col
    else:
        mapping = dict(mapping)
        mapping["cost_price_col"] = col
    await state.update_data(mapping=mapping)
    await callback.answer()
    await callback.message.edit_text(f"Колонка закупки: {col}")
    await prompt_parameter_setup(callback.message, state)


# --- Пресеты ---

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
            freight_cost=pr.freight_cost,
            manager_bonus_percent=pr.manager_bonus_percent,
            is_vat_included=pr.is_vat_included,
            target_margin_percent=pr.target_margin_percent,
        )
    await callback.answer(f"Пресет «{pr.name}»")
    await callback.message.edit_text(f"✅ Применён пресет: {pr.name}")
    # если в пресете уже есть цель — сразу к confirm
    if pr.target_margin_percent is not None:
        await state.set_state(CalcState.confirm_params)
        await callback.message.answer(
            f"Цель маржинальности: {pr.target_margin_percent}%\nНажмите «Рассчитать».",
            reply_markup=get_run_keyboard(),
        )
    else:
        await prompt_target_margin(callback.message, state)


@marginator_router.callback_query(F.data == "save_preset")
async def save_preset_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите название пресета (например, WB 18%):")
    await state.set_state(CalcState.save_preset_name)


@marginator_router.message(CalcState.save_preset_name)
async def save_preset_name(message: Message, state: FSMContext):
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


# --- Целевая маржа ---

@marginator_router.callback_query(CalcState.input_target_margin, F.data.startswith("target_m_"))
async def on_target_margin(callback: CallbackQuery, state: FSMContext):
    raw = callback.data or ""
    if raw == "target_m_skip":
        await state.update_data(target_margin_percent=None)
        await callback.answer("Без целевой маржи")
        msg = "Цель не задана."
    else:
        try:
            val = float(raw.split("_")[-1])
        except Exception:
            val = 25.0
        await state.update_data(target_margin_percent=val)
        await callback.answer()
        msg = f"Целевая маржинальность: {val}%"
    try:
        await callback.message.edit_text(msg)
    except Exception:
        pass
    await state.set_state(CalcState.confirm_params)
    await callback.message.answer(
        "Всё готово. Нажмите «🚀 Рассчитать».",
        reply_markup=get_run_keyboard(),
    )


# --- Кнопка «Рассчитать» (callback) ---

@marginator_router.callback_query(F.data == "run_calc")
async def execute_calculation_cb(callback: CallbackQuery, state: FSMContext):
    """Кнопка «Рассчитать»: from_user должен быть ПОЛЬЗОВАТЕЛЬ, не бот."""
    await callback.answer("Считаю…")

    class _Msg:
        def __init__(self, message, user):
            self.from_user = user  # callback.from_user — реальный пользователь
            self.chat = message.chat
            self.answer = message.answer
            self.answer_document = message.answer_document
            self.bot = message.bot
            self.message_id = message.message_id

    await execute_calculation(_Msg(callback.message, callback.from_user), state)



@marginator_router.callback_query(F.data == "cancel_flow")
async def cancel_flow_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    try:
        await callback.message.edit_text("Расчёт отменён.")
    except Exception:
        pass
    await callback.message.answer(
        "Можно начать заново.",
        reply_markup=get_main_reply_keyboard(),
    )
    await callback.message.answer("Режим:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.message(F.text.in_({"❌ Отмена", "Отмена", "/cancel"}))
async def cancel_flow_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Отменено. Нажмите «Новый расчёт» или выберите режим.",
        reply_markup=get_main_reply_keyboard(),
    )
    await message.answer("Режим:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.callback_query(F.data.startswith("tpl_"))
async def apply_mp_template(callback: CallbackQuery, state: FSMContext):
    key = (callback.data or "").replace("tpl_", "")
    tpl = MP_TEMPLATES.get(key)
    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await state.update_data(
        calc_mode="marketplace",
        commission_percent=tpl["commission_percent"],
        logistics_cost=tpl["logistics_cost"],
        packaging_cost=tpl["packaging_cost"],
        tax_rate_percent=tpl["tax_rate_percent"],
    )
    await callback.answer(tpl["label"])
    await callback.message.edit_text(
        f"✅ Шаблон «{tpl['label']}»\n\n"
        f"• Комиссия {tpl['commission_percent']}%\n"
        f"• Логистика {tpl['logistics_cost']} ₽\n"
        f"• Упаковка {tpl['packaging_cost']} ₽\n"
        f"• Налог (УСН) {tpl['tax_rate_percent']}%\n\n"
        "⚠️ Это ориентир для старта, не тариф маркетплейса.\n"
        "Подставьте свои цифры из кабинета МП или сохраните пресет."
    )
    await prompt_target_margin(callback.message, state)


@marginator_router.callback_query(F.data == "new_calc")
async def new_calc_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.answer(
        "🔄 **Новый расчёт**\nВыберите режим:",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown",
    )
    await callback.message.answer("Режим:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.callback_query(F.data == "show_history")
async def show_history_cb(callback: CallbackQuery):
    await callback.answer()
    await show_calculation_history(callback.message, telegram_id=callback.from_user.id)


@marginator_router.message(F.text.in_({"🔄 Новый расчёт", "Новый расчёт"}))
async def new_calc_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🔄 **Новый расчёт**\nВыберите режим:",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown",
    )
    await message.answer("Режим:", reply_markup=get_mode_keyboard())
    await state.set_state(CalcState.select_mode)


@marginator_router.message(F.text.in_({"📂 История", "История"}))
async def history_text(message: Message):
    await show_calculation_history(message)


@marginator_router.message(F.text.in_({"📖 Помощь", "Помощь", "/help"}))
async def help_text(message: Message):
    await cmd_help(message)


# --- Запуск расчёта (/run) ---

@marginator_router.message(CalcState.confirm_params, Command("run"))
@marginator_router.message(CalcState.confirm_params, F.text == "/run")
@marginator_router.message(CalcState.confirm_params, F.text.in_({
    "🚀 Рассчитать", "Рассчитать", "посчитать", "Посчитать",
}))
async def execute_calculation(message: Message, state: FSMContext):
    data = await state.get_data()

    file_bytes = await _load_file_bytes_from_state(data)
    if file_bytes is None or "mapping" not in data:
        await message.answer("❌ Данные файла потеряны. Нажмите «🔄 Новый расчёт» и загрузите файл снова.", reply_markup=get_main_reply_keyboard())
        await state.clear()
        return

    file_name = data.get("file_name", "price.xlsx")
    raw_mapping = data["mapping"]
    mapping = (
        TableMappingSchema.model_validate(raw_mapping)
        if isinstance(raw_mapping, dict)
        else raw_mapping
    )
    calc_mode = data.get("calc_mode", "marketplace")

    status_msg = await message.answer(
        "📊 **Считаю юнит-экономику и формирую отчёт...**", parse_mode="Markdown"
    )

    try:
        parser = ExcelParserService(api_key=os.getenv("XAI_API_KEY"))
        df = parser.load_normalized_dataframe(file_bytes, file_name, mapping)
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Ошибка чтения файла: `{e}`\n\n"
            "Для `.xls` нужен пакет **xlrd**. Или сохраните файл как **.xlsx**.",
            parse_mode="Markdown",
        )
        return

    # Сопоставляем имена колонок из mapping с реальными (регистр / пробелы)
    product_col = resolve_column(df, mapping.product_name_col)
    cost_col = resolve_column(df, mapping.cost_price_col)
    sell_col = resolve_column(df, mapping.selling_price_col)
    qty_col = resolve_column(df, mapping.quantity_col)

    # Всегда прогоняем эвристику: прайсы B2B часто имеют «Ед.» и ступени «-I- … руб.»
    detected = detect_columns_by_keywords(df)

    def _col_is_not_price(name: str | None) -> bool:
        if not name:
            return True
        n = str(name).strip().lower().replace("ё", "е")
        if n in ("ед", "ед.", "ед.изм", "ед. изм.", "unit", "uom"):
            return True
        if n.startswith("ед.") or n.startswith("ед "):
            return True
        bad = (
            "артикул", "sku", "barcode", "штрих", "категор", "бренд",
            "наимен", "назван", "остаток", "кол. в", "кол в уп",
        )
        return any(b in n for b in bad)

    if not product_col:
        product_col = detected.get("product_name_col")
    if not cost_col or _col_is_not_price(cost_col):
        cost_col = detected.get("cost_price_col") or cost_col
    # Если после эвристики всё ещё «Ед.» — ищем первую колонку с «руб»/«р.» в имени
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
        cols_preview = ", ".join(f"`{c}`" for c in list(df.columns)[:12])
        await status_msg.edit_text(
            "❌ Не найдены нужные колонки в файле.\n\n"
            f"Ожидали товар: `{mapping.product_name_col}` → "
            f"{'найдено: `' + str(product_col) + '`' if product_col else 'не найдено'}\n"
            f"Ожидали цену: `{mapping.cost_price_col}` → "
            f"{'найдено: `' + str(cost_col) + '`' if cost_col else 'не найдено'}\n\n"
            f"Колонки в файле: {cols_preview}\n\n"
            "Нужны колонки с **названием товара** и **закупочной ценой**.\n"
            "Отправьте /start и загрузите файл снова.",
            parse_mode="Markdown",
        )
        _cleanup_temp_file(data.get("file_path"))
        await state.clear()
        return

    results = []

    if calc_mode == "b2b":
        calc = B2BCalculator()
        freight = float(data.get("freight_cost", 0.0))
        bonus = float(data.get("manager_bonus_percent", 0.0))
        vat = bool(data.get("is_vat_included", True))

        total_rows = len(df)
        for n, (_, row) in enumerate(df.iterrows(), 1):
            try:
                raw_name = row.get(product_col)
                product_name = str(raw_name).strip() if pd.notna(raw_name) else ""
                if not product_name or product_name.lower() in (
                    "название", "наименование", "товар", "none", "nan",
                ):
                    continue
                cost_price = clean_numeric_value(row.get(cost_col))
                if cost_price <= 0:
                    continue
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
                if tgt is not None and calc_mode != "b2b":
                    mp = min_selling_price_for_margin(
                        item.cost_price,
                        target_margin_percent=float(tgt),
                        commission_percent=float(data.get("commission_percent", 15) or 15),
                        logistics_cost=float(data.get("logistics_cost", 0) or 0),
                        packaging_cost=float(data.get("packaging_cost", 0) or 0),
                    )
                    if mp is not None:
                        row_out[f"Мин. цена для маржи {tgt:g}%"] = mp
                results.append(row_out)

                if n == 1 or n % 250 == 0 or n == total_rows:
                    try:
                        await status_msg.edit_text(
                            f"📊 Считаю… {n}/{total_rows}"
                        )
                    except Exception:
                        pass
            except Exception:
                continue
    else:
        calc = MarketplaceCalculator()
        commission_percent = float(
            data.get("commission_percent", PurchasingConfig.DEFAULT_MP_COMMISSION_PCT)
        )
        logistics_cost = float(data.get("logistics_cost", PurchasingConfig.DEFAULT_LOGISTICS_RUB))
        packaging_cost = float(data.get("packaging_cost", PurchasingConfig.DEFAULT_PACKAGING_RUB))
        tax_rate_percent = float(data.get("tax_rate_percent", PurchasingConfig.DEFAULT_TAX_PCT))

        total_rows = len(df)
        for n, (_, row) in enumerate(df.iterrows(), 1):
            try:
                raw_name = row.get(product_col)
                product_name = str(raw_name).strip() if pd.notna(raw_name) else ""
                if not product_name or product_name.lower() in (
                    "название", "наименование", "товар", "none", "nan",
                ):
                    continue

                cost_price = clean_numeric_value(row.get(cost_col))
                if cost_price <= 0:
                    continue

                qty = 1
                if qty_col:
                    q = clean_numeric_value(row.get(qty_col))
                    if q >= 1:
                        qty = int(q)

                item = BaseItem(product_name=product_name, cost_price=cost_price, quantity=qty)

                if sell_col:
                    selling_price = clean_numeric_value(row.get(sell_col))
                    if selling_price <= 0:
                        selling_price = cost_price * (1 + PurchasingConfig.DEFAULT_MARKUP_PCT / 100.0)
                else:
                    selling_price = cost_price * (1 + PurchasingConfig.DEFAULT_MARKUP_PCT / 100.0)

                params = MarketplaceParams(
                    selling_price=selling_price,
                    commission_percent=commission_percent,
                    logistics_cost=logistics_cost,
                    packaging_cost=packaging_cost,
                    tax_rate_percent=tax_rate_percent,
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
                if tgt is not None and calc_mode != "b2b":
                    mp = min_selling_price_for_margin(
                        item.cost_price,
                        target_margin_percent=float(tgt),
                        commission_percent=float(data.get("commission_percent", 15) or 15),
                        logistics_cost=float(data.get("logistics_cost", 0) or 0),
                        packaging_cost=float(data.get("packaging_cost", 0) or 0),
                    )
                    if mp is not None:
                        row_out[f"Мин. цена для маржи {tgt:g}%"] = mp
                results.append(row_out)

                if n == 1 or n % 250 == 0 or n == total_rows:
                    try:
                        await status_msg.edit_text(
                            f"📊 Считаю… {n}/{total_rows}"
                        )
                    except Exception:
                        pass
            except Exception:
                continue

    if not results:
        cols_preview = ", ".join(f"`{c}`" for c in list(df.columns)[:12])
        await status_msg.edit_text(
            "❌ Не удалось рассчитать ни одной позиции.\n\n"
            f"Колонка товара: `{product_col}`, цена: `{cost_col}`\n"
            f"Строк в таблице: `{len(df)}`\n"
            f"Колонки: {cols_preview}\n\n"
            "Проверьте, что в колонке цены есть числа > 0 "
            "(не текст вроде «по запросу»).\n"
            "Либо сохраните прайс как **.xlsx** и загрузите снова через /start.",
            parse_mode="Markdown",
        )
        _cleanup_temp_file(data.get("file_path"))
        await state.clear()
        return

    df_results = pd.DataFrame(results)

    with SessionLocal() as db:
        upload_record = MarginatorDBService.save_calculation_results(
            db=db,
            telegram_id=int(message.from_user.id),
            filename=file_name,
            calc_mode=calc_mode,
            df_results=df_results,
        )
        upload_id = upload_record.id

    summary = AnalyticsService.generate_summary(df_results)
    summary_text = AnalyticsService.format_summary_message(summary)
    excel_bytes = ExcelExporterService.export_results_to_excel(df_results)
    # Всегда .xlsx: openpyxl пишет xlsx, а имя .xls Telegram/Excel не открывают
    out_name = Path(file_name).stem + "_marginator.xlsx"
    document = BufferedInputFile(excel_bytes, filename=out_name)

    await status_msg.delete()
    await message.answer(
        summary_text,
        reply_markup=get_webapp_keyboard(upload_id),
        parse_mode="Markdown",
    )
    await message.answer_document(
        document=document,
        caption="📥 Полный Excel-файл со всеми позициями и цветами.",
        parse_mode="Markdown",
    )

    # Файл на диске можно удалить, но mapping/params оставляем для «что если»
    # (повторный расчёт с другой комиссией)
    await state.update_data(last_upload_id=upload_id)
    await state.set_state(CalcState.confirm_params)



@marginator_router.callback_query(F.data.startswith("whatif_comm_"))
async def whatif_commission(callback: CallbackQuery, state: FSMContext):
    """Пересчёт с изменённой комиссией, не загружая файл заново."""
    data = await state.get_data()
    if "mapping" not in data and not data.get("file_path"):
        await callback.answer(
            "Нет данных прошлого расчёта. Сделайте новый расчёт.",
            show_alert=True,
        )
        return
    raw = callback.data or "whatif_comm_0"
    try:
        delta = float(raw.replace("whatif_comm_", "").replace("+", ""))
    except Exception:
        delta = 0.0
    mode = data.get("calc_mode", "marketplace")
    if mode == "b2b":
        await callback.answer("«Что если» по комиссии — только для маркетплейса", show_alert=True)
        return
    old = float(data.get("commission_percent", 15) or 15)
    new = max(0.0, min(80.0, old + delta))
    await state.update_data(commission_percent=new)
    await callback.answer(f"Комиссия {old:g}% → {new:g}%")
    await callback.message.answer(f"Пересчитываю с комиссией **{new:g}%**…", parse_mode="Markdown")

    class _Msg:
        def __init__(self, message, user):
            self.from_user = user
            self.chat = message.chat
            self.answer = message.answer
            self.answer_document = message.answer_document
            self.bot = message.bot
            self.message_id = message.message_id

    await execute_calculation(_Msg(callback.message, callback.from_user), state)


# --- /history ---

@marginator_router.message(Command("history"))
async def show_calculation_history(message: Message, telegram_id: int | None = None):
    uid = int(telegram_id) if telegram_id is not None else int(message.from_user.id)
    with SessionLocal() as db:
        uploads = MarginatorDBService.get_user_history(db, uid)

        if not uploads:
            await message.answer("📂 У вас пока нет сохранённых расчётов.")
            return

        await message.answer(
            f"📜 История расчётов ({len(uploads)}):\n"
            "Нажмите на файл — пришлю Excel повторно.",
            reply_markup=get_history_keyboard(uploads),
        )


# --- Файл не в том состоянии ---

@marginator_router.message(F.document)
async def handle_stray_document(message: Message):
    await message.answer(
        "🤔 Файл принят не вовремя.\n\n"
        "Нажмите **«🔄 Новый расчёт»** внизу или /start, затем снова отправьте файл.",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown",
    )


@marginator_router.callback_query(
    F.data.startswith("download_upload_") | F.data.startswith("hist_")
)
async def download_archived_report(callback: CallbackQuery):
    """Скачать Excel из истории. Всегда отвечаем на callback, чтобы не «висела» кнопка."""
    try:
        await callback.answer("Готовлю Excel…")
    except Exception:
        pass

    try:
        raw = callback.data or ""
        # download_upload_12  или  hist_12
        upload_id = int(raw.rsplit("_", 1)[-1])
    except Exception:
        await callback.message.answer("❌ Некорректный ID расчёта.")
        return

    try:
        with SessionLocal() as db:
            upload = MarginatorDBService.get_upload_with_items(db, upload_id)

            if not upload:
                await callback.message.answer(
                    "❌ Расчёт не найден в базе. Сделайте новый расчёт."
                )
                return

            owner_id = upload.user.telegram_id if upload.user else None
            if owner_id is not None and int(owner_id) != int(callback.from_user.id):
                await callback.message.answer("❌ Этот расчёт вам не принадлежит.")
                return

            items = list(upload.items or [])
            if not items:
                await callback.message.answer(
                    "❌ В этом расчёте нет сохранённых позиций.\n"
                    "Возможно, запись создалась без товаров. Запустите расчёт ещё раз."
                )
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
            caption=(
                f"📦 Архивный отчёт: {filename}\n"
                f"• Позиций: {len(items_data)}\n"
                f"• Выручка: {total_revenue:,.2f} ₽\n"
                f"• Прибыль: {total_profit:,.2f} ₽"
            ),
        )
    except Exception as e:
        await callback.message.answer(
            f"❌ Не удалось сформировать отчёт: {type(e).__name__}: {e}"
        )
