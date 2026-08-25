"""Единое чтение прайс-листов: csv / xlsx / xls + умный поиск колонок."""
from __future__ import annotations

import io
import re

import pandas as pd

_PRODUCT_KEYWORDS = (
    "назван", "наимен", "товар", "product", "name", "title", "item", "модель",
)
_COST_KEYWORDS = (
    "закуп", "себестоим", "cost", "purchase", "buy", "оптовая", "вход",
    "цена закуп", "price cost",
)
_SELL_KEYWORDS = (
    "продаж", "розниц", "rrp", "sell", "retail", "мрц", "ррц",
)
_QTY_KEYWORDS = (
    "остаток", "количест", "qty", "stock", "available", "шт",
)
_COST_NEGATIVE = (
    "артикул", "sku", "код", "barcode", "штрих", "категор", "бренд",
    "остаток", "кол-во", "количество",
)


def read_table(
    file_bytes: bytes,
    file_name: str,
    *,
    header: int | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    name = (file_name or "").lower()
    buffer = io.BytesIO(file_bytes)

    if name.endswith(".csv"):
        return pd.read_csv(buffer, header=header, nrows=nrows)

    if name.endswith(".xls") and not name.endswith(".xlsx"):
        try:
            return pd.read_excel(
                buffer, header=header, nrows=nrows, engine="xlrd"
            )
        except ImportError as e:
            raise RuntimeError(
                "Для файлов .xls установите пакет xlrd>=2.0.1 "
                "(pip install xlrd). Либо сохраните прайс как .xlsx."
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Не удалось прочитать .xls: {e}. "
                "Попробуйте открыть файл в Excel и сохранить как .xlsx."
            ) from e

    try:
        return pd.read_excel(
            buffer, header=header, nrows=nrows, engine="openpyxl"
        )
    except Exception:
        buffer.seek(0)
        return pd.read_excel(buffer, header=header, nrows=nrows)


def _norm(s: object) -> str:
    text = str(s) if s is not None else ""
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def resolve_column(df: pd.DataFrame, wanted: str | None) -> str | None:
    if not wanted:
        return None
    wanted_norm = _norm(wanted)
    if wanted_norm in ("", "nan", "none", "null", "unnamed"):
        return None

    cols = list(df.columns)
    for col in cols:
        if str(col) == wanted:
            return col
    for col in cols:
        if _norm(col) == wanted_norm:
            return col
    for col in cols:
        c = _norm(col)
        if not c or c.startswith("unnamed"):
            continue
        if wanted_norm in c or c in wanted_norm:
            return col
    return None


def _score_column(col_name: object, keywords: tuple[str, ...], negative: tuple[str, ...] = ()) -> int:
    n = _norm(col_name)
    if not n or n.startswith("unnamed") or n in ("nan", "none"):
        return -100
    for bad in negative:
        if bad in n:
            return -50
    score = 0
    for kw in keywords:
        if kw in n:
            score += 10 + len(kw)
    if "цен" in n or "price" in n:
        score += 3
    return score


def detect_columns_by_keywords(df: pd.DataFrame) -> dict[str, str | None]:
    cols = list(df.columns)
    product = cost = sell = qty = None
    best_p = best_c = best_s = best_q = -1

    for col in cols:
        sp = _score_column(col, _PRODUCT_KEYWORDS)
        if sp > best_p:
            best_p, product = sp, col

        sc = _score_column(col, _COST_KEYWORDS, _COST_NEGATIVE)
        if sc > best_c:
            best_c, cost = sc, col

        ss = _score_column(col, _SELL_KEYWORDS)
        if ss > best_s:
            best_s, sell = ss, col

        sq = _score_column(col, _QTY_KEYWORDS)
        if sq > best_q:
            best_q, qty = sq, col

    if best_c < 5:
        for col in cols:
            n = _norm(col)
            if ("цен" in n or "price" in n) and not any(b in n for b in _COST_NEGATIVE):
                cost = col
                best_c = 5
                break

    if best_p < 5:
        product = None
    if best_c < 5:
        cost = None
    if best_s < 5:
        sell = None
    if best_q < 5:
        qty = None

    return {
        "product_name_col": str(product) if product is not None else None,
        "cost_price_col": str(cost) if cost is not None else None,
        "selling_price_col": str(sell) if sell is not None else None,
        "quantity_col": str(qty) if qty is not None else None,
    }


def find_header_row(df_preview: pd.DataFrame, max_scan: int = 15) -> int:
    best_idx = 0
    best_score = -1
    rows = min(max_scan, len(df_preview))
    for i in range(rows):
        row = df_preview.iloc[i]
        score = 0
        non_empty = 0
        for val in row:
            n = _norm(val)
            if not n or n == "nan":
                continue
            non_empty += 1
            if re.fullmatch(r"[\d\s.,]+", n):
                score -= 2
                continue
            score += 2
            for kw in _PRODUCT_KEYWORDS + _COST_KEYWORDS + ("цена", "price", "артикул"):
                if kw in n:
                    score += 5
        score += non_empty
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx
