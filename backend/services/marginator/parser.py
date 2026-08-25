import io
import pandas as pd
from google import genai
from google.genai import types
from services.marginator.schemas import TableMappingSchema

class ExcelParserService:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    async def analyze_file_structure(self, file_bytes: bytes, file_name: str) -> TableMappingSchema:
        """
        Сканирует первые 20 строк файла с помощью Gemini 2.5 Flash
        и возвращает схему расположения ключевых колонок.
        """
        buffer = io.BytesIO(file_bytes)
        
        if file_name.endswith('.csv'):
            df_preview = pd.read_csv(buffer, header=None, nrows=20)
        else:
            df_preview = pd.read_excel(buffer, header=None, nrows=20)

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

        response = self.client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TableMappingSchema,
                temperature=0.0
            )
        )

        return TableMappingSchema.model_validate_json(response.text)

    def load_normalized_dataframe(
        self, 
        file_bytes: bytes, 
        file_name: str, 
        mapping: TableMappingSchema
    ) -> pd.DataFrame:
        """
        Загружает полный DataFrame с правильным заголовком и очищенными данными.
        """
        buffer = io.BytesIO(file_bytes)
        
        if file_name.endswith('.csv'):
            df = pd.read_csv(buffer, header=mapping.header_row_index)
        else:
            df = pd.read_excel(buffer, header=mapping.header_row_index)

        # Очистка имён колонок от пробелов и переносов строк
        df.columns = [str(col).strip() for col in df.columns]
        
        # Удаляем полностью пустые строки
        df = df.dropna(how='all')
        
        return df
