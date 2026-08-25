import os
import json
from google import genai
from google.genai import types

def extract_column_mapping(df_preview_json: str) -> dict:
    """
    Отправляет фрагмент таблицы в Gemini для авто-определения номеров колонок.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        # Резервные индексы по умолчанию, если API ключ не задан
        return {
            "title_col_index": 0,
            "price_col_index": 1,
            "sku_col_index": None,
            "stock_col_index": None,
            "data_start_row": 1
        }

    client = genai.Client(api_key=api_key)

    prompt = f"""
Проанализируй предпросмотр таблицы прайс-листа и определи индексы колонок (отсчет с 0):
1. title_col_index: номер колонки с наименованием товара.
2. price_col_index: номер колонки с ценой закупки.
3. sku_col_index: номер колонки с артикулом/кодом товара (null если нет).
4. stock_col_index: номер колонки с остатком/количеством (null если нет).
5. data_start_row: номер строки (отсчет с 0), с которой начинаются реальные товары.

JSON предпросмотр (первые строки):
{df_preview_json}

Верни ТОЛЬКО строго валидный JSON без лишнего текста:
{{
  "title_col_index": 0,
  "price_col_index": 1,
  "sku_col_index": null,
  "stock_col_index": null,
  "data_start_row": 1
}}
"""

    try:
        from services.marginator.llm_client import generate_json
        raw = generate_json(prompt, temperature=0.1)
        if not raw:
            raise RuntimeError("Gemini unavailable")
        return json.loads(raw)
    except Exception:
        return {
            "title_col_index": 0,
            "price_col_index": 1,
            "sku_col_index": None,
            "stock_col_index": None,
            "data_start_row": 1
        }
