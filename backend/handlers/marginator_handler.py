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
)
from database import SessionLocal
from services.marginator.parser import ExcelParserService
from services.marginator.calculators import (
    MarketplaceCalculator,
    MarketplaceParams,
    B2BCalculator,
    B2BParams,
    BaseItem,
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
    data = await state.get_data()
    _cleanup_temp_file(data.get("file_path"))
    await state.clear()

    await message.answer(
        "👋 **Привет! Я бот-маржинатор Trade Agent.**\n\n"
        "Я умею рассчитывать чистую прибыль, маржу и ROI для товаров на маркетплейсах и B2B.\n\n"
        "📌 **Как со мной работать:**\n"
        "1. Выберите режим расчёта.\n"
        "2. Отправьте Excel-файл (`.xlsx` / `.xls` / `.csv`) с прайс-листом.\n"
        "3. Задайте финансовые параметры.\n"
        "4. Получите Excel-отчёт и интерактивный дашборд.\n\n"
        "Команды:\n"
        "• `/history` — прошлые расчёты\n"
        "• `/help` — формат файлов\n"
        "• `/run` — запуск расчёта (после настройки параметров)",
        reply_markup=get_mode_keyboard(),
        parse_mode="Markdown",
    )
    await state.set_state(CalcState.select_mode)


@marginator_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 **Инструкция по загрузке прайс-листов:**\n\n"
        "• Бот автоматически распознаёт столбцы с помощью AI (Gemini), строгий шаблон не нужен.\n"
        "• Желательно наличие колонок с **названием товара** и **закупочной ценой**.\n"
        "• Опционально: цена продажи, количество.\n"
        "• Если в файле несколько листов, обрабатывается первый.\n"
        "• Форматы: `.xlsx`, `.xls`, `.csv`.\n"
        f"• Максимальный размер файла: **{MAX_FILE_SIZE_BYTES // (1024 * 1024)} МБ**.",
        parse_mode="Markdown",
    )


# --- Выбор режима ---

@marginator_router.callback_query(CalcState.select_mode, F.data.in_({"mode_marketplace", "mode_b2b"}))
async def select_calc_mode(callback: CallbackQuery, state: FSMContext):
    mode = "marketplace" if callback.data == "mode_marketplace" else "b2b"
    await state.update_data(calc_mode=mode)
    label = "Маркетплейс" if mode == "marketplace" else "B2B"
    await callback.message.edit_text(
        f"✅ Режим: **{label}**\n\n"
        "Теперь отправьте Excel-файл с прайс-листом (`.xlsx`, `.xls` или `.csv`).",
        parse_mode="Markdown",
    )
    await state.set_state(CalcState.upload_file)
    await callback.answer()


# --- Загрузка и анализ файла ---

@marginator_router.message(CalcState.upload_file, F.document)
async def handle_excel_upload(message: Message, state: FSMContext, bot: Bot):
    document = message.document
    file_name = (document.file_name or "price.xlsx").lower()

    if not (file_name.endswith(".xlsx") or file_name.endswith(".xls") or file_name.endswith(".csv")):
        await message.answer("Пожалуйста, отправьте файл формата `.xlsx`, `.xls` или `.csv`.")
        return

    if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
        await message.answer(
            f"❌ Файл слишком большой (**{document.file_size / (1024 * 1024):.1f} МБ**).\n"
            f"Максимум: **{MAX_FILE_SIZE_BYTES // (1024 * 1024)} МБ**."
        )
        return

    status_msg = await message.answer(
        "🔄 **Анализирую структуру файла через Gemini...**", parse_mode="Markdown"
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

    parser = ExcelParserService(api_key=os.getenv("GEMINI_API_KEY"))

    try:
        mapping = await parser.analyze_file_structure(file_bytes, file_name)

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

        await prompt_parameter_setup(message, state)

    except Exception as e:
        _cleanup_temp_file(temp_path)
        await status_msg.edit_text(f"❌ Ошибка при анализе файла: `{str(e)}`", parse_mode="Markdown")


async def prompt_parameter_setup(message: Message, state: FSMContext):
    """После сканирования файла — настройка параметров под выбранный режим."""
    data = await state.get_data()
    mode = data.get("calc_mode", "marketplace")

    if mode == "b2b":
        await message.answer(
            "⚙️ **Настройка B2B-параметров**\n\n"
            "Фрахт (доставка на единицу), бонус менеджера и учёт НДС:",
            reply_markup=get_b2b_params_keyboard(),
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            "⚙️ **Настройка финансовых параметров**\n\n"
            "Стандартные значения или ручной ввод:",
            reply_markup=get_params_setup_keyboard(),
            parse_mode="Markdown",
        )
    await state.set_state(CalcState.input_commission)


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
            "Отправьте `/run` для запуска расчёта.",
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
            f"✅ **Параметры установлены:**\n"
            f"• Комиссия: `{PurchasingConfig.DEFAULT_MP_COMMISSION_PCT}%`\n"
            f"• Логистика: `{PurchasingConfig.DEFAULT_LOGISTICS_RUB} ₽/ед.`\n"
            f"• Упаковка: `{PurchasingConfig.DEFAULT_PACKAGING_RUB} ₽/ед.`\n"
            f"• Налог (УСН): `{PurchasingConfig.DEFAULT_TAX_PCT}%`\n\n"
            f"Отправьте `/run` для запуска расчёта.",
            parse_mode="Markdown",
        )

    await state.set_state(CalcState.confirm_params)
    await callback.answer()


# --- Ручной пошаговый ввод (маркетплейс) ---

@marginator_router.callback_query(CalcState.input_commission, F.data == "params_custom")
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
            f"Отправьте `/run` для запуска генерации отчёта.",
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.confirm_params)
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
            f"Отправьте `/run` для запуска генерации отчёта.",
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.confirm_params)
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
            f"Отправьте `/run` для запуска генерации отчёта.",
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.confirm_params)

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
            f"Отправьте `/run` для запуска генерации отчёта.",
            parse_mode="Markdown",
        )
        await state.set_state(CalcState.confirm_params)

    await callback.answer()


# --- Запуск расчёта (/run) ---

@marginator_router.message(CalcState.confirm_params, Command("run"))
@marginator_router.message(CalcState.confirm_params, F.text == "/run")
async def execute_calculation(message: Message, state: FSMContext):
    data = await state.get_data()

    file_bytes = await _load_file_bytes_from_state(data)
    if file_bytes is None or "mapping" not in data:
        await message.answer("❌ Данные файла потеряны. Отправьте /start и загрузите файл заново.")
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
        parser = ExcelParserService(api_key=os.getenv("GEMINI_API_KEY"))
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

    # Если mapping сбился (nan / Артикул вместо цены) — автодетект по ключевым словам
    detected = detect_columns_by_keywords(df)
    cost_looks_like_sku = bool(
        cost_col and any(
            x in str(cost_col).lower()
            for x in ("артикул", "sku", "barcode", "штрих", "код ")
        )
    )
    if not product_col:
        product_col = detected.get("product_name_col")
    if not cost_col or cost_looks_like_sku:
        cost_col = detected.get("cost_price_col") or cost_col
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

        for _, row in df.iterrows():
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
                results.append({
                    "Товар": item.product_name,
                    "Себестоимость, ₽": item.cost_price,
                    "Выручка, ₽": res.revenue,
                    "Чистая прибыль, ₽": res.net_profit,
                    "Маржинальность %": res.margin_percent,
                    "ROI %": res.roi_percent,
                })
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

        for _, row in df.iterrows():
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
                results.append({
                    "Товар": item.product_name,
                    "Себестоимость, ₽": item.cost_price,
                    "Выручка, ₽": res.revenue,
                    "Чистая прибыль, ₽": res.net_profit,
                    "Маржинальность %": res.margin_percent,
                    "ROI %": res.roi_percent,
                })
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
            telegram_id=message.from_user.id,
            filename=file_name,
            calc_mode=calc_mode,
            df_results=df_results,
        )
        upload_id = upload_record.id

    summary = AnalyticsService.generate_summary(df_results)
    summary_text = AnalyticsService.format_summary_message(summary)
    excel_bytes = ExcelExporterService.export_results_to_excel(df_results)
    document = BufferedInputFile(excel_bytes, filename=f"Marginator_{file_name}")

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

    _cleanup_temp_file(data.get("file_path"))
    await state.clear()


# --- /history ---

@marginator_router.message(Command("history"))
async def show_calculation_history(message: Message):
    with SessionLocal() as db:
        uploads = MarginatorDBService.get_user_history(db, message.from_user.id)

        if not uploads:
            await message.answer("📂 У вас пока нет сохранённых расчётов.")
            return

        await message.answer(
            "📜 **История ваших последних расчётов:**\n"
            "Выберите отчёт для повторного скачивания файла Excel:",
            reply_markup=get_history_keyboard(uploads),
            parse_mode="Markdown",
        )


# --- Файл не в том состоянии ---

@marginator_router.message(F.document)
async def handle_stray_document(message: Message):
    await message.answer(
        "🤔 Похоже, вы отправили файл не вовремя.\n\n"
        "Отправьте команду /start, чтобы начать новый расчёт заново.",
    )


@marginator_router.callback_query(F.data.startswith("download_upload_"))
async def download_archived_report(callback: CallbackQuery):
    upload_id = int(callback.data.split("_")[-1])
    await callback.answer("⏳ Генерирую отчёт из БД...")

    with SessionLocal() as db:
        upload = MarginatorDBService.get_upload_with_items(db, upload_id)

        if not upload or not upload.items:
            await callback.message.answer("❌ Данные этого расчёта не найдены.")
            return

        if not upload.user or upload.user.telegram_id != callback.from_user.id:
            await callback.message.answer("❌ Этот расчёт вам не принадлежит.")
            return

        items_data = [
            {
                "Товар": item.title,
                "Себестоимость, ₽": item.buy_price,
                "Выручка, ₽": item.est_sell_price,
                "Чистая прибыль, ₽": item.net_profit,
                "Маржинальность %": item.margin_pct,
                "ROI %": item.roi_pct,
            }
            for item in upload.items
        ]

        df_results = pd.DataFrame(items_data)
        excel_bytes = ExcelExporterService.export_results_to_excel(df_results)
        document = BufferedInputFile(excel_bytes, filename=f"Archive_{upload.filename}")

        await callback.message.answer_document(
            document=document,
            caption=(
                f"📦 **Архивный отчёт:** `{upload.filename}`\n"
                f"• Выручка: `{upload.total_revenue:,.2f} ₽`\n"
                f"• Прибыль: `{upload.total_profit:,.2f} ₽`"
            ),
            parse_mode="Markdown",
        )
