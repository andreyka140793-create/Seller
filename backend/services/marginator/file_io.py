"""Единое чтение прайс-листов: csv / xlsx / xls."""
from __future__ import annotations

import io

import pandas as pd


def read_table(
    file_bytes: bytes,
    file_name: str,
    *,
    header: int | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    """
    Читает таблицу из байтов файла.
    - .csv  → pandas read_csv
    - .xlsx → openpyxl
    - .xls  → xlrd (>=2.0.1)
    """
    name = (file_name or "").lower()
    buffer = io.BytesIO(file_bytes)

    if name.endswith(".csv"):
        return pd.read_csv(buffer, header=header, nrows=nrows)

    if name.endswith(".xls") and not name.endswith(".xlsx"):
        # Старый бинарный Excel 97-2003 — нужен xlrd
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

    # .xlsx и прочее
    try:
        return pd.read_excel(
            buffer, header=header, nrows=nrows, engine="openpyxl"
        )
    except Exception:
        # fallback без явного engine
        buffer.seek(0)
        return pd.read_excel(buffer, header=header, nrows=nrows)


def resolve_column(df: pd.DataFrame, wanted: str | None) -> str | None:
    """
    Находит колонку в DataFrame по имени из mapping.
    Сначала точное совпадение, затем без учёта регистра / пробелов.
    """
    if not wanted:
        return None
    cols = list(df.columns)
    if wanted in cols:
        return wanted
    wanted_norm = str(wanted).strip().lower().replace("\n", " ")
    for col in cols:
        if str(col).strip().lower().replace("\n", " ") == wanted_norm:
            return col
    # частичное вхождение
    for col in cols:
        c = str(col).strip().lower()
        if wanted_norm in c or c in wanted_norm:
            return col
    return None
