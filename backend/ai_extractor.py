"""Определение индексов колонок через Grok (xAI). Без google-genai."""
import json
import os

from services.marginator.llm_client import generate_json


def extract_column_mapping(df_preview_json: str) -> dict:
    """
    Фрагмент таблицы → индексы колонок.
    Без XAI_API_KEY возвращает значения по умолчанию.
    """
    default = {
        "title_col_index": 0,
        "price_col_index": 1,
        "sku_col_index": None,
        "stock_col_index": None,
        "data_start_row": 1,
    }
    if not os.getenv("XAI_API_KEY"):
        return default

    prompt = f"""
Проанализируй предпросмотр таблицы прайс-листа и определи индексы колонок (отсчёт с 0):
1. title_col_index: колонка с наименованием товара
2. price_col_index: колонка с ценой закупки (не артикул, не Ед.)
3. sku_col_index: артикул/код (null если нет)
4. stock_col_index: остаток/количество (null если нет)
5. data_start_row: строка, с которой начинаются товары

JSON предпросмотр:
{df_preview_json}

Верни ТОЛЬКО валидный JSON:
{{
  "title_col_index": 0,
  "price_col_index": 1,
  "sku_col_index": null,
  "stock_col_index": null,
  "data_start_row": 1
}}
"""
    try:
        raw = generate_json(prompt, temperature=0.1)
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default
