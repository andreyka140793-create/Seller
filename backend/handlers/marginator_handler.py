import os
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from states.marginator_states import CalcState
from services.marginator.parser import ExcelParserService
from aiogram.types import BufferedInputFile
from services.marginator.calculators import MarketplaceCalculator, MarketplaceParams, BaseItem
from services.marginator.exporter import ExcelExporterService

@marginator_router.message(CalcState.input_params, F.text == "/run")
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
    
    for _, row in df.iterrows():
        try:
            item = BaseItem(
                product_name=str(row[mapping['product_name_col']]),
                cost_price=float(row[mapping['cost_price_col']])
            )
            # Пример параметров (в реальном FSM берутся из введенных пользователем данных)
            params = MarketplaceParams(selling_price=float(row.get('Цена', 1000)), commission_percent=15, logistics_cost=120)
            
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

    df_results = pd.DataFrame(results)
    
    # 3. Генерация .xlsx отчета с цветовой разметкой
    excel_bytes = ExcelExporterService.export_results_to_excel(df_results)
    
    # 4. Отправка документа в Telegram
    document = BufferedInputFile(excel_bytes, filename=f"Marginator_{file_name}")
    
    await status_msg.delete()
    await message.answer_document(
        document=document,
        caption="✅ **Расчет окончен!**\n\nТовары с маржой < 5% подсвечены красным, > 20% — зеленым.",
        parse_mode="Markdown"
    )
    await state.clear()
marginator_router = Router()

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
            file_bytes=file_bytes.hex(), # Кодируем для сохранения в FSM storage
            file_name=file_name,
            mapping=mapping.model_dump()
        )
        
        await status_msg.edit_text(
            f"✅ **Структура определена!**\n\n"
            f"• Строка шапки: `{mapping.header_row_index + 1}`\n"
            f"• Колонка товара: `{mapping.product_name_col}`\n"
            f"• Колонка себестоимости: `{mapping.cost_price_col}`\n\n"
            f"Введите параметры комиссии/логистики или отправьте `/run` для расчета.",
            parse_mode="Markdown"
        )
        await state.set_state(CalcState.input_params)

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при анализе файла: `{str(e)}`", parse_mode="Markdown")
