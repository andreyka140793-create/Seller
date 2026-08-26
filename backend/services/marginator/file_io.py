"""Unified table reading and smart column detection."""
from __future__ import annotations
import io
import re
import pandas as pd

_PRODUCT_KEYWORDS = (
    "наимен", "назван", "товар", "product", "name", "title", "item", "модель",
)
_COST_KEYWORDS = (
    "закуп", "себестоим", "cost", "purchase", "buy", "оптовая", "входн",
    "цена закуп", "price cost", "цена,", "цена ",
)
_PRICE_TIER_HINTS = (
    "руб", "р.", "₽", "rub", "price", "цен",
    "от ", "до ", "-i-", "-ii-", "-iii-", "-iv-",
    "опт", "парт",
)
_SELL_KEYWORDS = (
    "продаж", "розниц", "rrp", "sell", "retail", "мрц", "ррц",
)
_QTY_KEYWORDS = (
    "остаток", "количест", "qty", "stock", "available", "кол. в", "кол в уп",
)
_COST_NEGATIVE = (
    "артикул", "sku", "barcode", "штрих", "категор", "бренд",
    "остаток", "кол-во", "количество", "кол. в", "кол в уп",
    "ед.", "ед ", "единиц", "unit", "изм",
    "код", "№", "номер",
)


def read_table(file_bytes: bytes, file_name: str, *, header: int | None = None, nrows: int | None = None) -> pd.DataFrame:
    name = (file_name or "").lower()
    buffer = io.BytesIO(file_bytes)
    if name.endswith(".csv"):
        return pd.read_csv(buffer, header=header, nrows=nrows)
    if name.endswith(".xls") and not name.endswith(".xlsx"):
        try:
            return pd.read_excel(buffer, header=header, nrows=nrows, engine="xlrd")
        except ImportError as e:
            raise RuntimeError("Install xlrd>=2.0.1 for .xls files") from e
    try:
        return pd.read_excel(buffer, header=header, nrows=nrows, engine="openpyxl")
    except Exception:
        buffer.seek(0)
        return pd.read_excel(buffer, header=header, nrows=nrows)


def _norm(s: object) -> str:
    text = str(s) if s is not None else ""
    text = text.replace("
", " ").replace("", " ")
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


def _is_unit_column(col_name: object) -> bool:
    n = _norm(col_name)
    if n in ("ед", "ед.", "ед.изм", "ед. изм.", "unit", "uom"):
        return True
    if n.startswith("ед.") or n.startswith("ед "):
        return True
    if "единиц" in n and "цен" not in n:
        return True
    return False


def _score_column(col_name: object, keywords: tuple[str, ...], negative: tuple[str, ...] = ()) -> int:
    n = _norm(col_name)
    if not n or n.startswith("unnamed") or n in ("nan", "none"):
        return -100
    if _is_unit_column(col_name):
        return -100
    for bad in negative:
        if bad in n:
            return -50
    score = 0
    for kw in keywords:
        if kw in n:
            score += 10 + len(kw)
    return score


def _score_price_column(col_name: object) -> int:
    n = _norm(col_name)
    if not n or n.startswith("unnamed") or n in ("nan", "none"):
        return -100
    if _is_unit_column(col_name):
        return -100
    for bad in _COST_NEGATIVE:
        if bad in n and "цен" not in n and "руб" not in n:
            return -50
    score = 0
    for kw in _COST_KEYWORDS:
        if kw in n:
            score += 15 + len(kw)
    for kw in _PRICE_TIER_HINTS:
        if kw in n:
            score += 8
    if re.search(r"\d+\s*р", n) or re.search(r"\d+\s*руб", n):
        score += 20
    if re.search(r"-i+-|-ii+-|-iii+-|-iv+-", n) or re.search(r"i+|ii+|iii+", n):
        score += 12
    if "от " in n and "до " in n:
        score += 10
    return score


def detect_columns_by_keywords(df: pd.DataFrame) -> dict[str, str | None]:
    cols = list(df.columns)
    product = cost = sell = qty = None
    best_p = best_c = best_s = best_q = -1
    for col in cols:
        sp = _score_column(col, _PRODUCT_KEYWORDS)
        if sp > best_p:
            best_p, product = sp, col
        sc = _score_price_column(col)
        if sc > best_c:
            best_c, cost = sc, col
        ss = _score_column(col, _SELL_KEYWORDS)
        if ss > best_s:
            best_s, sell = ss, col
        sq = _score_column(col, _QTY_KEYWORDS, ("единиц",))
        if sq > best_q:
            best_q, qty = sq, col

    if best_c < 8 and len(df) > 0:
        best_numeric = -1.0
        numeric_col = None
        for col in cols:
            if _is_unit_column(col):
                continue
            n = _norm(col)
            if any(b in n for b in ("артикул", "sku", "код", "наимен", "назван", "категор")):
                continue
            series = df[col]
            ok = total = 0
            for v in series.head(30):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    continue
                total += 1
                s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
                s = re.sub(r"[^\d.]", "", s)
                try:
                    if s and float(s) > 0:
                        ok += 1
                except ValueError:
                    pass
            ratio = ok / total if total else 0
            if ratio > best_numeric and ratio >= 0.4:
                best_numeric = ratio
                numeric_col = col
        if numeric_col is not None and best_c < 8:
            cost = numeric_col
            best_c = 8

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
            for kw in _PRODUCT_KEYWORDS + _COST_KEYWORDS + ("цена", "price", "артикул", "руб"):
                if kw in n:
                    score += 5
        score += non_empty
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def list_price_tier_columns(df) -> list[str]:
    scored = []
    for col in df.columns:
        sc = _score_price_column(col)
        if sc >= 8:
            scored.append((sc, str(col)))
    scored.sort(key=lambda x: -x[0])
    seen = set()
    out = []
    for _, name in scored:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def detect_weight_column(df) -> str | None:
    best, score = None, 0
    for col in df.columns:
        n = str(col).strip().lower().replace("ё", "е")
        sc = 0
        if any(x in n for x in ("вес", "weight", "кг", "kg", "масса")):
            sc += 15
        if "г." in n or n.endswith(" г") or "грамм" in n:
            sc += 5
        if sc > score:
            score, best = sc, str(col)
    return best if score >= 15 else None
