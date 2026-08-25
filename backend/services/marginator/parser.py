import pandas as pd
from google import genai
from google.genai import types
from services.marginator.schemas import TableMappingSchema
from services.marginator.file_io import (
    read_table,
    find_header_row,
    detect_columns_by_keywords,
    _norm,
)


def _is_bad_name(name: object) -> bool:
    n = _norm(name)
    return not n or n in ("nan", "none", "null") or n.startswith("unnamed")


class ExcelParserService:
    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key) if api_key else None

    async def analyze_file_structure(self, file_bytes: bytes, file_name: str) -> TableMappingSchema:
        df_preview = read_table(file_bytes, file_name, header=None, nrows=25)

        if df_preview.empty or df_preview.shape[1] == 0:
            raise RuntimeError("Файл пустой или не содержит колонок.")

        header_idx = find_header_row(df_preview)

        df_headed = read_table(file_bytes, file_name, header=header_idx, nrows=5)
        df_headed.columns = [
            str(c).strip().replace("\n", " ") for c in df_headed.columns
        ]
        detected = detect_columns_by_keywords(df_headed)

        cols = list(df_headed.columns)
        product = detected["product_name_col"] or (str(cols[0]) if cols else "Товар")
        cost = detected["cost_price_col"]
        if not cost:
            for c in cols[1:]:
                cn = _norm(c)
                if any(x in cn for x in ("артикул", "sku", "ед.", "единиц", "кол. в")):
                    continue
                if any(x in cn for x in ("цен", "price", "cost", "руб", "₽", "р.")):
                    cost = str(c)
                    break
            if not cost and len(cols) > 1:
                cost = str(cols[min(3, len(cols) - 1)])

        default_mapping = TableMappingSchema(
            header_row_index=header_idx,
            product_name_col=product,
            cost_price_col=cost or (str(cols[1]) if len(cols) > 1 else "Цена"),
            selling_price_col=detected.get("selling_price_col"),
            commission_col=None,
            quantity_col=detected.get("quantity_col"),
        )

        if not self.client:
            return default_mapping

        raw_preview = df_preview.head(20).to_string()
        prompt = f"""
Проанализируй фрагмент прайс-листа и определи структуру таблицы.

Данные файла (первые строки, индекс 0 = первая строка файла):
{raw_preview}

Задача:
1. header_row_index — индекс строки (0-based) с ЗАГОЛОВКАМИ столбцов.
2. product_name_col — ТОЧНОЕ имя колонки с названием товара.
3. cost_price_col — ТОЧНОЕ имя колонки с ЗАКУПОЧНОЙ ценой (не артикул, не остаток).
4. selling_price_col — розничная цена, если есть, иначе null.
5. quantity_col — остаток/количество, если есть, иначе null.

Не путай «Артикул» с ценой. Цена: закуп, цена, ₽, руб.
"""

        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TableMappingSchema,
                    temperature=0.0,
                ),
            )
            ai = TableMappingSchema.model_validate_json(response.text)
        except Exception:
            return default_mapping

        try:
            h = int(ai.header_row_index)
            if 0 <= h < 20:
                df_ai = read_table(file_bytes, file_name, header=h, nrows=3)
                df_ai.columns = [
                    str(c).strip().replace("\n", " ") for c in df_ai.columns
                ]
            else:
                df_ai = df_headed
                h = header_idx
        except Exception:
            df_ai = df_headed
            h = header_idx

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

        product_final = pick(ai.product_name_col, default_mapping.product_name_col)
        cost_final = pick(ai.cost_price_col, default_mapping.cost_price_col)

        if cost_final and (
            any(x in _norm(cost_final) for x in ("артикул", "sku", "barcode", "штрих"))
            or _norm(cost_final) in ("ед", "ед.", "unit")
            or _norm(cost_final).startswith("ед.")
        ):
            cost_final = default_mapping.cost_price_col

        if not cost_final or _is_bad_name(cost_final):
            again = detect_columns_by_keywords(df_ai)
            cost_final = again.get("cost_price_col") or cost_final
            product_final = product_final or again.get("product_name_col")

        return TableMappingSchema(
            header_row_index=h,
            product_name_col=product_final or default_mapping.product_name_col,
            cost_price_col=cost_final or default_mapping.cost_price_col,
            selling_price_col=pick(ai.selling_price_col, detected.get("selling_price_col")),
            commission_col=None,
            quantity_col=pick(ai.quantity_col, detected.get("quantity_col")),
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

        df = read_table(file_bytes, file_name, header=header_row_index)
        df.columns = [str(col).strip().replace("\n", " ") for col in df.columns]
        df = df.dropna(how="all")
        return df
