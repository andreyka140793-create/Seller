import os
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from states.marginator_states import CalcState
from services.marginator.parser import ExcelParserService

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
