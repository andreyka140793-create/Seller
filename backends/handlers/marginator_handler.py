import os
import pandas as pd
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext

from states.marginator_states import CalcState
from keyboards.marginator_keyboards import (
    get_params_setup_keyboard,
    get_skip_keyboard,
    get_history_keyboard,
    get_webapp_keyboard,
)
from database import SessionLocal
from services.marginator.parser import ExcelParserService
from services.marginator.calculators import MarketplaceCalculator, MarketplaceParams, BaseItem
from services.marginator.exporter import ExcelExporterService
from services.marginator.db_service import MarginatorDBService
from services.marginator.analytics import AnalyticsService

marginator_router = Router()


# --- Команда /start ---

@marginator_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 **Привет! Я бот-маржинатор Trade Agent.**\n\n"
        "Я умею рассчитывать чистую прибыль, маржу и ROI для товаров на маркетплейсах и B2B.\n\n"
        "📌 **Как со мной работать:**\n"
        "1. Отправьте мне Excel-файл (`.xlsx` или `.xls`) с прайс-листом.\n"
        "2. Выберите режим расчета и задайте финансовые параметры.\n"
        "3. Получите готовый Excel-отчет и интерактивный дашборд.\n\n"
        "Команды:\n"
        "• `/history` — Посмотреть прошлые расчеты\n"
        "• `/help` — Инструкция по формату файлов",
        parse_mode="Markdown"
    )


@marginator_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 **Инструкция по загрузке прайс-листов:**\n\n"
        "• Бот автоматически распознает столбцы с помощью AI (Gemini), поэтому строгий шаблон не требуется.\n"
        "• Желательно, чтобы в таблице были колонки с **названием товара** и **закупочной ценой**.\n"
        "• Если в файле несколько листов, обработан будет первый лист.\n"
        "• Поддерживаемые форматы: `.xlsx`, `.xls`.",
        parse_mode="Markdown"
    )


# --- Загрузка и анализ файла ---

@marginator_router.message(CalcState.upload_file, F.document)
async def handle_excel_upload(message: Message, state: FSMContext, bot: Bot):
    document = message.document
    file_name = document.file_name.lower()

    if not (file_name.endswith('.xlsx') or file_name.endswith('.xls') or file_name.endswith('.csv')):
        await message.answer("Пожалуйста, отправьте файл формата `.xlsx`, `.xls` или `.csv`.")
        return

    status_msg = await message.answer("🔄 **Анализирую структуру файла через Gemini...**", parse_mode="Markdown")

    # Скачивание файла в оперативную память
    file_io = await bot.download(document.file_id)
    file_bytes = file_io.read()

    # Инициализация парсера
    parser = ExcelParserService(api_key=os.getenv("GEMINI_API_KEY"))

    try:
        mapping = await parser.analyze_file_structure(file_bytes, file_name)

        # Сохраняем схему и бинарник во временный контекст FSM (или S3 / локальный кеш)
        await state.update_data(
            file_bytes=file_bytes.hex(),  # Кодируем для сохранения в FSM storage
            file_name=file_name,
            mapping=mapping.model_dump()
        )

        await status_msg.edit_text(
            f"✅ **Структура определена!**\n\n"
            f"• Строка шапки: `{mapping.header_row_index + 1}`\n"
            f"• Колонка товара: `{mapping.product_name_col}`\n"
            f"• Колонка себестоимости: `{mapping.cost_price_col}`",
            parse_mode="Markdown"
        )

        # Переход к настройке финансовых параметров
        await prompt_parameter_setup(message, state)

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при анализе файла: `{str(e)}`", parse_mode="Markdown")


# --- Переход к выборке параметров после успешного парсинга таблицы ---

async def prompt_parameter_setup(message: Message, state: FSMContext):
    """Вызывается после завершения сканирования файла Gemini."""
    await message.answer(
        "⚙️ **Настройка финансовых параметров**\n\n"
        "Вы можете использовать стандартные параметры или задать свои значение вручную:",
        reply_markup=get_params_setup_keyboard(),
        parse_mode="Markdown"
    )
    await state.set_state(CalcState.input_commission)


# --- Быстрый выбор по умолчанию ---

@marginator_router.callback_query(CalcState.input_commission, F.data == "params_default")
async def apply_default_params(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        commission_percent=15.0,
        logistics_cost=120.0,
        tax_rate_percent=6.0
    )
    await callback.message.edit_text(
        "✅ **Параметры установлены:**\n"
        "• Комиссия: `15%`\n"
        "• Логистика: `120 ₽/ед.`\n"
        "• Налог (УСН): `6%`\n\n"
        "Отправьте `/run` для запуска расчета.",
        parse_mode="Markdown"
    )
    await state.set_state(CalcState.confirm_params)


# --- Ручной пошаговый ввод ---

@marginator_router.callback_query(CalcState.input_commission, F.data == "params_custom")
async def start_custom_params(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "1️⃣ **Введите комиссию маркетплейса в %** (например, `12.5`):",
        reply_markup=get_skip_keyboard(),
        parse_mode="Markdown"
    )


@marginator_router.message(CalcState.input_commission)
async def process_commission_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(',', '.'))
        if not (0 <= val <= 100):
            raise ValueError
        await state.update_data(commission_percent=val)
        await message.answer(
            "2️⃣ **Введите среднюю логистику на единицу товара в ₽** (например, `80`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown"
        )
        await state.set_state(CalcState.input_logistics)
    except ValueError:
        await message.answer("❌ Введите корректное число от 0 до 100.")


@marginator_router.message(CalcState.input_logistics)
async def process_logistics_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(',', '.'))
        if val < 0:
            raise ValueError
        await state.update_data(logistics_cost=val)
        await message.answer(
            "3️⃣ **Введите ставку налога в %** (например, `6` для УСН 6% или `15` для УСН 15%):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown"
        )
        await state.set_state(CalcState.input_tax)
    except ValueError:
        await message.answer("❌ Введите неотрицательное число.")


@marginator_router.message(CalcState.input_tax)
async def process_tax_input(message: Message, state: FSMContext):
    try:
        val = float(message.text.replace(',', '.'))
        if not (0 <= val <= 100):
            raise ValueError
        await state.update_data(tax_rate_percent=val)

        data = await state.get_data()
        await message.answer(
            f"✅ **Все параметры сохранены!**\n\n"
            f"• Комиссия: `{data.get('commission_percent')}%`\n"
            f"• Логистика: `{data.get('logistics_cost')} ₽`\n"
            f"• Налог: `{data.get('tax_rate_percent')}%`\n\n"
            f"Отправьте `/run` для запуска генерации отчета.",
            parse_mode="Markdown"
        )
        await state.set_state(CalcState.confirm_params)
    except ValueError:
        await message.answer("❌ Введите корректную процентную ставку.")


# --- Кнопка «Пропустить» на любом из шагов ручного ввода параметров ---

@marginator_router.callback_query(F.data == "skip_param")
async def skip_optional_param(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state == CalcState.input_commission.state:
        await state.update_data(commission_percent=0.0)
        await callback.message.edit_text(
            "2️⃣ **Введите среднюю логистику на единицу товара в ₽** (например, `80`):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown"
        )
        await state.set_state(CalcState.input_logistics)

    elif current_state == CalcState.input_logistics.state:
        await state.update_data(logistics_cost=0.0)
        await callback.message.edit_text(
            "3️⃣ **Введите ставку налога в %** (например, `6` для УСН 6% или `15` для УСН 15%):",
            reply_markup=get_skip_keyboard(),
            parse_mode="Markdown"
        )
        await state.set_state(CalcState.input_tax)

    elif current_state == CalcState.input_tax.state:
        await state.update_data(tax_rate_percent=0.0)
        data = await state.get_data()
        await callback.message.edit_text(
            f"✅ **Все параметры сохранены!**\n\n"
            f"• Комиссия: `{data.get('commission_percent', 0.0)}%`\n"
            f"• Логистика: `{data.get('logistics_cost', 0.0)} ₽`\n"
            f"• Налог: `0%`\n\n"
            f"Отправьте `/run` для запуска генерации отчета.",
            parse_mode="Markdown"
        )
        await state.set_state(CalcState.confirm_params)

    await callback.answer()


# --- Запуск расчета ---

@marginator_router.message(CalcState.confirm_params, F.text == "/run")
async def execute_calculation(message: Message, state: FSMContext):
    data = await state.get_data()

    file_bytes = bytes.fromhex(data['file_bytes'])
    file_name = data['file_name']
    mapping = data['mapping']

    status_msg = await message.answer("📊 **Считаю юнит-экономику и формирую отчет...**", parse_mode="Markdown")

    # 1. Загрузка DataFrame через ранее написанный парсер
    parser = ExcelParserService(api_key=os.getenv("GEMINI_API_KEY"))
    df = parser.load_normalized_dataframe(file_bytes, file_name, mapping)

    # 2. Проход по строкам и расчет метрик
    calc = MarketplaceCalculator()
    results = []

    commission_percent = data.get("commission_percent", 15.0)
    logistics_cost = data.get("logistics_cost", 120.0)
    tax_rate_percent = data.get("tax_rate_percent", 6.0)

    for _, row in df.iterrows():
        try:
            item = BaseItem(
                product_name=str(row[mapping['product_name_col']]),
                cost_price=float(row[mapping['cost_price_col']])
            )
            selling_price = float(row.get('Цена', item.cost_price * 2))
            params = MarketplaceParams(
                selling_price=selling_price,
                commission_percent=commission_percent,
                logistics_cost=logistics_cost,
                tax_rate_percent=tax_rate_percent
            )

            res = calc.calculate_item(item, params)
            results.append({
                "Товар": item.product_name,
                "Себестоимость, ₽": item.cost_price,
                "Выручка, ₽": res.revenue,
                "Чистая прибыль, ₽": res.net_profit,
                "Маржинальность %": res.margin_percent,
                "ROI %": res.roi_percent
            })
        except Exception:
            continue

    if not results:
        await status_msg.edit_text("❌ Не удалось рассчитать ни одной позиции. Проверьте структуру файла.")
        await state.clear()
        return

    df_results = pd.DataFrame(results)

    # 3. Сохраняем расчет в БД и получаем объект upload_record
    with SessionLocal() as db:
        upload_record = MarginatorDBService.save_calculation_results(
            db=db,
            telegram_id=message.from_user.id,
            filename=file_name,
            calc_mode=data.get("calc_mode", "marketplace"),
            df_results=df_results
        )
        upload_id = upload_record.id

    # 4. Формируем документы и текст
    summary = AnalyticsService.generate_summary(df_results)
    summary_text = AnalyticsService.format_summary_message(summary)
    excel_bytes = ExcelExporterService.export_results_to_excel(df_results)
    document = BufferedInputFile(excel_bytes, filename=f"Marginator_{file_name}")

    # 5. Отправляем карточку с прикрепленной кнопкой Mini App и Excel-документ
    await status_msg.delete()
    await message.answer(
        summary_text,
        reply_markup=get_webapp_keyboard(upload_id),
        parse_mode="Markdown"
    )
    await message.answer_document(
        document=document,
        caption="📥 Полный Excel-файл со всеми позициями и цветами.",
        parse_mode="Markdown"
    )
    await state.clear()


# --- Обработчик команды /history ---

@marginator_router.message(Command("history"))
async def show_calculation_history(message: Message):
    with SessionLocal() as db:
        uploads = MarginatorDBService.get_user_history(db, message.from_user.id)

        if not uploads:
            await message.answer("📂 У вас пока нет сохраненных расчетов.")
            return

        await message.answer(
            "📜 **История ваших последних расчетов:**\n"
            "Выберите отчет для повторного скачивания файла Excel:",
            reply_markup=get_history_keyboard(uploads),
            parse_mode="Markdown"
        )


# --- Обработчик клика по кнопке скачивания из истории ---

@marginator_router.callback_query(F.data.startswith("download_upload_"))
async def download_archived_report(callback: CallbackQuery):
    upload_id = int(callback.data.split("_")[-1])
    await callback.answer("⏳ Генерирую отчет из БД...")

    with SessionLocal() as db:
        upload = MarginatorDBService.get_upload_with_items(db, upload_id)

        if not upload or not upload.items:
            await callback.message.answer("❌ Данные этого расчета не найдены.")
            return

        # Проверяем, что архивный расчет принадлежит тому, кто его запрашивает
        if not upload.user or upload.user.telegram_id != callback.from_user.id:
            await callback.message.answer("❌ Этот расчет вам не принадлежит.")
            return

        # Восстановление данных для экспорта
        items_data = [
            {
                "Товар": item.title,
                "Себестоимость, ₽": item.buy_price,
                "Выручка, ₽": item.est_sell_price,
                "Чистая прибыль, ₽": item.net_profit,
                "Маржинальность %": item.margin_pct,
                "ROI %": item.roi_pct
            }
            for item in upload.items
        ]

        df_results = pd.DataFrame(items_data)
        excel_bytes = ExcelExporterService.export_results_to_excel(df_results)
        document = BufferedInputFile(excel_bytes, filename=f"Archive_{upload.filename}")

        await callback.message.answer_document(
            document=document,
            caption=(
                f"📦 **Архивный отчет:** `{upload.filename}`\n"
                f"• Выручка: `{upload.total_revenue:,.2f} ₽`\n"
                f"• Прибыль: `{upload.total_profit:,.2f} ₽`"
            ),
            parse_mode="Markdown"
        )
