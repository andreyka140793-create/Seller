"""Парсинг прайса: таблица / текст / картинка → TableMappingSchema + DataFrame."""
from __future__ import annotations

import io
import json
import logging

import pandas as pd

from services.marginator.schemas import TableMappingSchema
from services.marginator.file_io import (
    read_table,
    find_header_row,
    detect_columns_by_keywords,
    _norm,
)
from services.marginator.document_loader import LoadedDocument, load_document
from services.marginator.llm_client import generate_json, generate_json_from_image

logger = logging.getLogger(__name__)


def _is_bad_name(name: object) -> bool:
    n = _norm(name)
    return not n or n in ("nan", "none", "null") or n.startswith("unnamed")


class ExcelParserService:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key  # совместимость; реально XAI_API_KEY

    async def analyze_file_structure(self, file_bytes: bytes, file_name: str) -> TableMappingSchema:
        doc = load_document(file_bytes, file_name)

        if doc.kind == "image":
            return await self._analyze_image(doc)

        if doc.kind == "text":
            return await self._analyze_text(doc)

        # table
        assert doc.dataframe is not None
        df_preview = doc.dataframe.head(25).copy()
        if df_preview.empty or df_preview.shape[1] == 0:
            raise RuntimeError("Файл пустой или не содержит колонок.")

        header_idx = find_header_row(df_preview)
        # Перечитываем с header для keyword-детекции
        try:
            df_headed = read_table(file_bytes, file_name, header=header_idx, nrows=8)
        except Exception:
            df_headed = df_preview.copy()
            if header_idx < len(df_headed):
                df_headed.columns = [
                    str(x).strip() for x in df_headed.iloc[header_idx].tolist()
                ]
                df_headed = df_headed.iloc[header_idx + 1 :].reset_index(drop=True)

        df_headed.columns = [str(c).strip().replace("\n", " ") for c in df_headed.columns]
        detected = detect_columns_by_keywords(df_headed)
        cols = list(df_headed.columns)

        product = detected["product_name_col"] or (str(cols[0]) if cols else "Товар")
        cost = detected["cost_price_col"]
        if not cost:
            for c in cols:
                cn = _norm(c)
                if any(x in cn for x in ("артикул", "sku", "ед.", "единиц", "кол. в", "код")):
                    continue
                if any(x in cn for x in ("цен", "price", "cost", "руб", "₽", "р.")):
                    cost = str(c)
                    break
            if not cost and len(cols) > 1:
                cost = str(cols[min(3, len(cols) - 1)])

        default_mapping = TableMappingSchema(
            header_row_index=int(header_idx),
            product_name_col=product,
            cost_price_col=cost or (str(cols[1]) if len(cols) > 1 else "Цена"),
            selling_price_col=detected.get("selling_price_col"),
            commission_col=None,
            quantity_col=detected.get("quantity_col"),
        )

        # Grok (опционально) уточняет mapping
        raw_preview = df_preview.head(20).to_string()
        prompt = f"""
Проанализируй фрагмент прайс-листа и верни JSON:
{{
  "header_row_index": <int 0-based строка заголовков>,
  "product_name_col": "<точное имя колонки товара>",
  "cost_price_col": "<точное имя колонки ЗАКУПОЧНОЙ цены, не артикул и не Ед.>",
  "selling_price_col": <string|null>,
  "commission_col": null,
  "quantity_col": <string|null>
}}

Данные (первые строки):
{raw_preview}

Важно: «Ед.» = единица измерения, не цена. Цена часто «руб», «-I- от … р.», «Закупочная цена».
"""
        raw = generate_json(prompt, temperature=0.0)
        if not raw:
            return default_mapping

        try:
            ai = TableMappingSchema.model_validate_json(raw)
        except Exception:
            return default_mapping

        return self._merge_ai_mapping(ai, default_mapping, df_headed, file_bytes, file_name)

    async def _analyze_text(self, doc: LoadedDocument) -> TableMappingSchema:
        text = (doc.text or "")[:8000]
        prompt = f"""
Из текста прайса извлеки структуру. Верни JSON:
{{
  "header_row_index": 0,
  "product_name_col": "Товар",
  "cost_price_col": "Цена",
  "selling_price_col": null,
  "commission_col": null,
  "quantity_col": null
}}
Если в тексте таблица — укажи реальные имена колонок.
Текст:
{text}
"""
        raw = generate_json(prompt, temperature=0.0)
        if raw:
            try:
                return TableMappingSchema.model_validate_json(raw)
            except Exception:
                pass
        return TableMappingSchema(
            header_row_index=0,
            product_name_col="Товар",
            cost_price_col="Цена",
            selling_price_col=None,
            commission_col=None,
            quantity_col=None,
        )

    async def _analyze_image(self, doc: LoadedDocument) -> TableMappingSchema:
        prompt = """
На изображении прайс-лист. Верни JSON:
{
  "header_row_index": 0,
  "product_name_col": "Товар",
  "cost_price_col": "Цена",
  "selling_price_col": null,
  "commission_col": null,
  "quantity_col": null
}
Имена колонок — как на картинке, если читаются. Цена ≠ артикул ≠ единица измерения.
"""
        raw = generate_json_from_image(
            prompt,
            doc.image_bytes or b"",
            doc.image_mime or "image/jpeg",
            temperature=0.0,
        )
        if raw:
            try:
                return TableMappingSchema.model_validate_json(raw)
            except Exception:
                pass
        return TableMappingSchema(
            header_row_index=0,
            product_name_col="Товар",
            cost_price_col="Цена",
            selling_price_col=None,
            commission_col=None,
            quantity_col=None,
        )

    def _merge_ai_mapping(
        self,
        ai: TableMappingSchema,
        default: TableMappingSchema,
        df_headed: pd.DataFrame,
        file_bytes: bytes,
        file_name: str,
    ) -> TableMappingSchema:
        try:
            h = int(ai.header_row_index)
            if 0 <= h < 20:
                try:
                    df_ai = read_table(file_bytes, file_name, header=h, nrows=3)
                    df_ai.columns = [str(c).strip().replace("\n", " ") for c in df_ai.columns]
                except Exception:
                    df_ai = df_headed
                    h = default.header_row_index
            else:
                df_ai = df_headed
                h = default.header_row_index
        except Exception:
            df_ai = df_headed
            h = default.header_row_index

        col_names = {_norm(c): str(c) for c in df_ai.columns}

        def pick(ai_name: str | None, fallback: str | None) -> str | None:
            if ai_name and not _is_bad_name(ai_name):
                if ai_name in list(df_ai.columns):
                    return ai_name
                key = _norm(ai_name)
                if key in col_names:
                    return col_names[key]
                for c in df_ai.columns:
                    cn = _norm(c)
                    if key in cn or cn in key:
                        return str(c)
            return fallback

        product_final = pick(ai.product_name_col, default.product_name_col)
        cost_final = pick(ai.cost_price_col, default.cost_price_col)

        if cost_final and (
            any(x in _norm(cost_final) for x in ("артикул", "sku", "barcode", "штрих"))
            or _norm(cost_final) in ("ед", "ед.", "unit")
            or _norm(cost_final).startswith("ед.")
        ):
            cost_final = default.cost_price_col

        if not cost_final or _is_bad_name(cost_final):
            again = detect_columns_by_keywords(df_ai)
            cost_final = again.get("cost_price_col") or cost_final
            product_final = product_final or again.get("product_name_col")

        return TableMappingSchema(
            header_row_index=h,
            product_name_col=product_final or default.product_name_col,
            cost_price_col=cost_final or default.cost_price_col,
            selling_price_col=pick(ai.selling_price_col, default.selling_price_col),
            commission_col=None,
            quantity_col=pick(ai.quantity_col, default.quantity_col),
        )

    def load_normalized_dataframe(
        self,
        file_bytes: bytes,
        file_name: str,
        mapping: TableMappingSchema | dict,
    ) -> pd.DataFrame:
        if isinstance(mapping, dict):
            header_row_index = int(mapping["header_row_index"])
        else:
            header_row_index = mapping.header_row_index

        doc = load_document(file_bytes, file_name)

        if doc.kind == "table" and doc.dataframe is not None:
            # Для csv/xls уже прочитано без header — перечитаем с header
            try:
                df = read_table(file_bytes, file_name, header=header_row_index)
            except Exception:
                df = doc.dataframe.copy()
                if header_row_index < len(df):
                    df.columns = [str(x).strip() for x in df.iloc[header_row_index].tolist()]
                    df = df.iloc[header_row_index + 1 :].reset_index(drop=True)
            df.columns = [str(col).strip().replace("\n", " ") for col in df.columns]
            return df.dropna(how="all")

        if doc.kind == "text" and doc.text:
            # LLM/эвристика: строим 2 колонки Товар/Цена из строк
            return self._text_to_dataframe(doc.text, mapping)

        if doc.kind == "image":
            return self._image_to_dataframe(doc, mapping)

        raise RuntimeError("Не удалось получить таблицу из файла")

    def _text_to_dataframe(self, text: str, mapping: TableMappingSchema | dict) -> pd.DataFrame:
        prompt = f"""
Извлеки из текста прайса список товаров. Верни JSON:
{{"items": [{{"name": "...", "price": 123.45}}]}}
Только позиции с числовой ценой. Текст:
{text[:10000]}
"""
        raw = generate_json(prompt, temperature=0.0)
        items = []
        if raw:
            try:
                data = json.loads(raw)
                items = data.get("items") or data.get("rows") or []
            except Exception:
                items = []
        if not items:
            # простой разбор строк «название ... 123»
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = None
                import re
                m = re.search(r"(.+?)\s+([\d\s]+[.,]\d{2}|\d+)\s*$", line)
                if m:
                    try:
                        price = float(m.group(2).replace(" ", "").replace(",", "."))
                        items.append({"name": m.group(1).strip(), "price": price})
                    except ValueError:
                        pass
        if not items:
            raise RuntimeError("Не удалось извлечь товары из текста. Пришлите Excel/CSV.")

        product_col = "Товар"
        cost_col = "Цена"
        if isinstance(mapping, dict):
            product_col = mapping.get("product_name_col") or product_col
            cost_col = mapping.get("cost_price_col") or cost_col
        else:
            product_col = mapping.product_name_col or product_col
            cost_col = mapping.cost_price_col or cost_col

        rows = [{product_col: it.get("name"), cost_col: it.get("price")} for it in items]
        return pd.DataFrame(rows)

    def _image_to_dataframe(self, doc: LoadedDocument, mapping: TableMappingSchema | dict) -> pd.DataFrame:
        prompt = """
С изображения прайса извлеки товары. Верни JSON:
{"items": [{"name": "...", "price": 123.45}]}
Только строки с числовой ценой (закупка/опт).
"""
        raw = generate_json_from_image(
            prompt,
            doc.image_bytes or b"",
            doc.image_mime or "image/jpeg",
            temperature=0.0,
        )
        items = []
        if raw:
            try:
                data = json.loads(raw)
                items = data.get("items") or []
            except Exception:
                items = []
        if not items:
            raise RuntimeError(
                "Не удалось распознать товары на изображении. "
                "Пришлите более чёткий скриншот или Excel."
            )
        product_col = "Товар"
        cost_col = "Цена"
        if isinstance(mapping, dict):
            product_col = mapping.get("product_name_col") or product_col
            cost_col = mapping.get("cost_price_col") or cost_col
        else:
            product_col = mapping.product_name_col or product_col
            cost_col = mapping.cost_price_col or cost_col
        rows = [{product_col: it.get("name"), cost_col: it.get("price")} for it in items]
        return pd.DataFrame(rows)
