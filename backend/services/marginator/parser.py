import io
import pandas as pd
from google import genai
from google.genai import types
from services.marginator.schemas import TableMappingSchema


class ExcelParserService:
    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key) if api_key else None

    async def analyze_file_structure(self, file_bytes: bytes, file_name: str) -> TableMappingSchema:
        """
        Сканирует первые 20 строк файла с помощью Gemini 2.5 Flash
        и возвращает схему расположения ключевых колонок.
        """
        buffer = io.BytesIO(file_bytes)

        if file_name.endswith(".csv"):
            df_preview = pd.read_csv(buffer, header=None, nrows=20)
        else:
            df_preview = pd.read_excel(buffer, header=None, nrows=20)

        # Эвристика по умолчанию, если нет API-ключа или Gemini недоступен
        default_mapping = TableMappingSchema(
            header_row_index=0,
            product_name_col=str(df_preview.iloc[0, 0]) if df_preview.shape[1] > 0 else "Товар",
            cost_price_col=str(df_preview.iloc[0, 1]) if df_preview.shape[1] > 1 else "Цена",
            selling_price_col=None,
            commission_col=None,
            quantity_col=None,
        )

        if not self.client:
            return default_mapping

        raw_preview = df_preview.to_string()

        prompt = f"""
        Проанализируй фрагмент прайс-листа и определи структуру таблицы.

        Данные файла (первые 20 строк):
        {raw_preview}

        Задача:
        1. Найди индекс строки (0-based), которая содержит заголовки столбцов.
        2. Найди точное название колонки с товаром/артикулом.
        3. Найди точное название колонки с себестоимостью или закупочной ценой.
        4. Если есть, укажи названия колонок цены продажи, комиссии и количества.
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
            return TableMappingSchema.model_validate_json(response.text)
        except Exception:
            return default_mapping

    def load_normalized_dataframe(
        self,
        file_bytes: bytes,
        file_name: str,
        mapping: TableMappingSchema | dict,
    ) -> pd.DataFrame:
        """
        Загружает полный DataFrame с правильным заголовком и очищенными данными.
        Принимает как TableMappingSchema, так и dict (после FSM model_dump).
        """
        if isinstance(mapping, dict):
            header_row_index = int(mapping["header_row_index"])
        else:
            header_row_index = mapping.header_row_index

        buffer = io.BytesIO(file_bytes)

        if file_name.endswith(".csv"):
            df = pd.read_csv(buffer, header=header_row_index)
        else:
            df = pd.read_excel(buffer, header=header_row_index)

        # Очистка имён колонок от пробелов и переносов строк
        df.columns = [str(col).strip() for col in df.columns]

        # Удаляем полностью пустые строки
        df = df.dropna(how="all")

        return df
