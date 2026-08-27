"""Compare two price lists."""
from pathlib import Path
from aiogram import F
from aiogram.types import Message, BufferedInputFile
from aiogram.fsm.context import FSMContext

from handlers.marginator.router import marginator_router
from states.marginator_states import CalcState
from keyboards.marginator_keyboards import get_main_reply_keyboard
from services.marginator.document_loader import is_supported
from services.marginator.file_io import read_table
from services.marginator.compare import compare_price_lists
from services.marginator.exporter import ExcelExporterService
from services.marginator.db_service import MarginatorDBService
from database import SessionLocal

_UPLOAD_ROOT = Path("/data/uploads") if Path("/data").exists() else Path(__import__("tempfile").gettempdir()) / "marginator_uploads"


@marginator_router.message(CalcState.compare_upload_a, F.document)
async def compare_receive_a(message: Message, state: FSMContext, bot):
    doc = message.document
    name = (doc.file_name or "price_a.xlsx").lower()
    if not is_supported(name):
        from services.marginator.document_loader import supported_formats_hint
        await message.answer(f"Нужен поддерживаемый прайс: {supported_formats_hint()}")
        return
    if doc.file_size and doc.file_size > 20*1024*1024:
        await message.answer("Файл слишком большой.")
        return
    tg = await bot.get_file(doc.file_id)
    buf = await bot.download_file(tg.file_path)
    raw = buf.read()
    path = _UPLOAD_ROOT / f"cmp_a_{message.from_user.id}_{doc.file_unique_id}{Path(name).suffix}"
    path.write_bytes(raw)
    await state.update_data(compare_a_path=str(path), compare_a_name=doc.file_name or name)
    await message.answer(f"Прайс A: {doc.file_name}\n\nТеперь отправьте **второй** прайс (B).")
    await state.set_state(CalcState.compare_upload_b)


@marginator_router.message(CalcState.compare_upload_b, F.document)
async def compare_receive_b(message: Message, state: FSMContext, bot):
    doc = message.document
    name = (doc.file_name or "price_b.xlsx").lower()
    if not is_supported(name):
        from services.marginator.document_loader import supported_formats_hint
        await message.answer(f"Нужен поддерживаемый прайс: {supported_formats_hint()}")
        return
    tg = await bot.get_file(doc.file_id)
    buf = await bot.download_file(tg.file_path)
    raw = buf.read()
    path_b = _UPLOAD_ROOT / f"cmp_b_{message.from_user.id}_{doc.file_unique_id}{Path(name).suffix}"
    path_b.write_bytes(raw)

    data = await state.get_data()
    path_a = data.get("compare_a_path")
    name_a = data.get("compare_a_name") or "A"
    name_b = doc.file_name or name
    if not path_a or not Path(path_a).is_file():
        await message.answer("Первый файл потерян. Начните сравнение заново.")
        await state.clear()
        return

    status = await message.answer("⚖️ Сравниваю прайсы…")
    try:
        df_a = read_table(Path(path_a).read_bytes(), name_a)
        df_b = read_table(raw, name_b)
        result = compare_price_lists(df_a, df_b, label_a="A", label_b="B")
        if result.empty:
            await status.edit_text("Не удалось сопоставить позиции.")
            return

        both = result[result["Статус"] == "есть в обоих"]
        cheaper_b = len(both[both["Вывод"] == "B выгоднее"]) if len(both) else 0
        cheaper_a = len(both[both["Вывод"] == "A выгоднее"]) if len(both) else 0
        only_a = len(result[result["Статус"] == "только в A"])
        only_b = len(result[result["Статус"] == "только в B"])

        excel = ExcelExporterService.export_results_to_excel(result)
        out = BufferedInputFile(excel, filename="compare_prices.xlsx")
        await status.delete()
        await message.answer(
            f"⚖️ Результат сравнения\n• A: {name_a}\n• B: {name_b}\n"
            f"• Совпало: {len(both)}\n• B выгоднее: {cheaper_b}\n• A выгоднее: {cheaper_a}\n"
            f"• Только в A: {only_a}\n• Только в B: {only_b}"
        )
        await message.answer_document(out, caption="Сравнение: разница ₽ и %.")
    except Exception:
        await status.edit_text("Ошибка сравнения.")
    finally:
        Path(path_a).unlink(missing_ok=True)
        path_b.unlink(missing_ok=True)
        await state.clear()
        await message.answer("Готово.", reply_markup=get_main_reply_keyboard())
