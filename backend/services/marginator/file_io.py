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


def sniff_bytes(file_bytes: bytes) -> str:
    """Определить тип по сигнатуре, а не только по расширению."""
    if not file_bytes:
        return "empty"
    head = file_bytes[:256]
    if head[:2] == b"PK":
        return "zip"  # xlsx/xlsm/ods
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"  # xls / doc
    low = head.lstrip().lower()
    if low.startswith(b"<?xml") or b"<yml" in low or b"<shop" in low:
        return "xml"
    if low.startswith(b"<!doctype html") or low.startswith(b"<html"):
        return "html"
    if low.startswith(b"{") or low.startswith(b"["):
        return "json"
    # CSV-ish
    sample = file_bytes[:4000]
    try:
        text = sample.decode("utf-8")
    except Exception:
        try:
            text = sample.decode("cp1251")
        except Exception:
            text = ""
    if text and (";" in text or "," in text or "\t" in text):
        return "csv"
    return "unknown"


def _score_dataframe(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return -1.0
    rows, cols = df.shape
    if cols < 2 or rows < 1:
        return -1.0
    # сколько ячеек похожи на числа
    sample = df.iloc[: min(40, rows), : min(12, cols)]
    numeric = 0
    total = 0
    for col in sample.columns:
        for val in sample[col].tolist():
            total += 1
            s = str(val).strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
            if not s or s.lower() in ("nan", "none"):
                continue
            try:
                float(re.sub(r"[^\d.\-]", "", s) or "x")
                numeric += 1
            except Exception:
                pass
    return rows * 0.01 + cols * 0.5 + (numeric / max(total, 1)) * 10.0


def _read_excel_multisheet(file_bytes: bytes, *, header: int | None, nrows: int | None, engines: list[str]) -> pd.DataFrame:
    last_err: Exception | None = None
    for engine in engines:
        try:
            buf = io.BytesIO(file_bytes)
            xl = pd.ExcelFile(buf, engine=engine)
            best_df = None
            best_score = -1.0
            for sheet in xl.sheet_names[:12]:
                try:
                    df = pd.read_excel(xl, sheet_name=sheet, header=header, nrows=nrows, dtype=str)
                    sc = _score_dataframe(df)
                    if sc > best_score:
                        best_score = sc
                        best_df = df
                except Exception as e:
                    last_err = e
                    continue
            if best_df is not None and best_score >= 0:
                return best_df
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("Не удалось прочитать ни один лист Excel")


def read_table(file_bytes: bytes, file_name: str, *, header: int | None = None, nrows: int | None = None) -> pd.DataFrame:
    """Устойчивое чтение таблиц: сигнатура файла, несколько движков, все листы."""
    name = (file_name or "").lower()
    kind = sniff_bytes(file_bytes)

    # HTML под видом xls/xlsx
    if kind == "html" or name.endswith((".html", ".htm")):
        try:
            tables = pd.read_html(io.BytesIO(file_bytes))
            if tables:
                tables = sorted(tables, key=_score_dataframe, reverse=True)
                return tables[0].astype(str)
        except Exception as e:
            raise RuntimeError(f"HTML-таблица не прочитана: {e}") from e

    # XML/YML
    if kind == "xml" or name.endswith((".xml", ".yml", ".yaml")):
        from services.marginator.document_loader import _read_xml_price
        return _read_xml_price(file_bytes)

    # JSON
    if kind == "json" or name.endswith(".json"):
        from services.marginator.document_loader import _read_json_price
        return _read_json_price(file_bytes)

    # CSV / text
    if name.endswith((".csv", ".dat", ".tsv", ".txt")) or kind == "csv":
        sep = "\t" if name.endswith(".tsv") else None
        last = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            for separator in ((sep,) if sep else (";", ",", "\t", "|", None)):
                try:
                    kw = dict(header=header, nrows=nrows, dtype=str, engine="python", encoding=enc)
                    if separator is None:
                        kw["sep"] = None
                    else:
                        kw["sep"] = separator
                    df = pd.read_csv(io.BytesIO(file_bytes), **kw)
                    if df.shape[1] >= 1:
                        return df
                except Exception as e:
                    last = e
                    continue
        if last:
            raise RuntimeError(f"CSV/текст не прочитан: {last}") from last
        raise RuntimeError("CSV/текст пуст или не распознан")

    # Excel binary OLE (.xls)
    if name.endswith(".xls") and not name.endswith((".xlsx", ".xlsm", ".xlsb")) or kind == "ole":
        try:
            return _read_excel_multisheet(file_bytes, header=header, nrows=nrows, engines=["xlrd"])
        except Exception as e1:
            # иногда .xls на самом деле HTML
            try:
                tables = pd.read_html(io.BytesIO(file_bytes))
                if tables:
                    return sorted(tables, key=_score_dataframe, reverse=True)[0].astype(str)
            except Exception:
                pass
            raise RuntimeError(
                f"Не удалось прочитать .xls: {e1}. "
                "Сохраните как .xlsx в Excel и пришлите снова."
            ) from e1

    # xlsb
    if name.endswith(".xlsb"):
        try:
            return _read_excel_multisheet(file_bytes, header=header, nrows=nrows, engines=["pyxlsb"])
        except Exception as e:
            raise RuntimeError(f"Не удалось прочитать .xlsb: {e}. Сохраните как .xlsx.") from e

    # ods
    if name.endswith(".ods"):
        try:
            return _read_excel_multisheet(file_bytes, header=header, nrows=nrows, engines=["odf"])
        except Exception as e:
            raise RuntimeError(f"Не удалось прочитать .ods: {e}") from e

    # xlsx / xlsm / zip-based
    if name.endswith((".xlsx", ".xlsm")) or kind == "zip":
        try:
            return _read_excel_multisheet(
                file_bytes, header=header, nrows=nrows, engines=["openpyxl"]
            )
        except Exception as e:
            # password / corrupt
            msg = str(e).lower()
            if "password" in msg or "encrypted" in msg:
                raise RuntimeError("Файл защищён паролем. Снимите защиту и пришлите снова.") from e
            raise RuntimeError(f"Не удалось прочитать Excel: {e}") from e

    # fallback chain
    errors = []
    for eng in ("openpyxl", "xlrd"):
        try:
            return _read_excel_multisheet(file_bytes, header=header, nrows=nrows, engines=[eng])
        except Exception as e:
            errors.append(f"{eng}: {e}")
    try:
        return pd.read_csv(io.BytesIO(file_bytes), header=header, nrows=nrows, sep=None, engine="python")
    except Exception as e:
        errors.append(f"csv: {e}")
    raise RuntimeError("Файл не прочитан. " + " | ".join(errors[:3]))


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
    if re.search(r"-i+-|-ii+-|-iii+-|-iv+-", n) or re.search(r"\bi+\b|\bii+\b|\biii+\b", n):
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
                s = str(v).replace(" ", "").replace("\xa0", "").replace(",", ".")
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
