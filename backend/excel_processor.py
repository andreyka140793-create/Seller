import io
import pandas as pd
from ai_extractor import extract_column_mapping
from math_engine import calculate_unit_economics
from config import PurchasingConfig
from services.marginator.utils import clean_numeric_value

def process_excel_file(
    file_bytes: bytes,
    filename: str,
    logistics_cost: float = PurchasingConfig.DEFAULT_LOGISTICS_RUB,
    packaging_cost: float = PurchasingConfig.DEFAULT_PACKAGING_RUB,
    mp_commission_pct: float = PurchasingConfig.DEFAULT_MP_COMMISSION_PCT,
    tax_pct: float = PurchasingConfig.DEFAULT_TAX_PCT,
    markup_pct: float = PurchasingConfig.DEFAULT_MARKUP_PCT
) -> list[dict]:
    """
    Чтение файла, вызов ИИ для определения колонок и расчет экономики для каждой строки.
    """
    from services.marginator.file_io import read_table
    df_raw = read_table(file_bytes, filename, header=None)

    df_raw = df_raw.where(pd.notnull(df_raw), None)

    # Берём первые 15 строк для ИИ-анализа
    preview_df = df_raw.iloc[:15].copy()
    preview_json = preview_df.to_json(orient="values", force_ascii=False)

    mapping = extract_column_mapping(preview_json)

    title_idx = mapping.get("title_col_index", 0)
    price_idx = mapping.get("price_col_index", 1)
    sku_idx = mapping.get("sku_col_index")
    start_row = mapping.get("data_start_row", 1)

    df_data = df_raw.iloc[start_row:].copy()
    processed_items = []

    for _, row in df_data.iterrows():
        try:
            title = str(row.iloc[title_idx]).strip() if pd.notnull(row.iloc[title_idx]) else ""
            if not title or title.lower() in ["название", "наименование", "товар", "none", "nan"]:
                continue

            raw_price = row.iloc[price_idx] if pd.notnull(row.iloc[price_idx]) else None
            buy_price = clean_numeric_value(raw_price)

            if buy_price <= 0:
                continue

            sku = str(row.iloc[sku_idx]).strip() if sku_idx is not None and pd.notnull(row.iloc[sku_idx]) else None
            target_sell_price = round(buy_price * (1 + markup_pct / 100.0), 2)

            econ = calculate_unit_economics(
                buy_price=buy_price,
                target_sell_price=target_sell_price,
                logistics_cost=logistics_cost,
                packaging_cost=packaging_cost,
                mp_commission_pct=mp_commission_pct,
                tax_pct=tax_pct
            )

            processed_items.append({
                "title": title,
                "sku": sku,
                "buy_price": buy_price,
                "est_sell_price": target_sell_price,
                "net_profit": econ["net_profit"],
                "margin_pct": econ["margin_pct"],
                "roi_pct": econ["roi_pct"],
                "is_profitable": econ["is_profitable"]
            })
        except Exception:
            continue

    return processed_items
