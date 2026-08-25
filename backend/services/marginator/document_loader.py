"""
Загрузка прайсов из разных форматов → единый DataFrame или сырой текст для LLM.
Поддержка: xlsx, xls, csv, tsv, txt, docx, pdf, jpeg/png/webp (vision).
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

import pandas as pd

from services.marginator.file_io import read_table

logger = logging.getLogger(__name__)

TABLE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}
TEXT_EXTENSIONS = {".txt", ".text", ".md", ".csv", ".tsv"}
DOC_EXTENSIONS = {".docx", ".doc"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


@dataclass
class LoadedDocument:
    kind: str  # table | text | image
    file_name: str
    dataframe: pd.DataFrame | None = None
    text: str | None = None
    image_bytes: bytes | None = None
    image_mime: str | None = None


def extension_of(file_name: str) -> str:
    name = (file_name or "").lower().strip()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def is_supported(file_name: str) -> bool:
    ext = extension_of(file_name)
    return ext in (
        TABLE_EXTENSIONS
        | TEXT_EXTENSIONS
        | DOC_EXTENSIONS
        | PDF_EXTENSIONS
        | IMAGE_EXTENSIONS
        | {".doc"}  # предупредим, что .doc лучше docx
    )


def load_document(file_bytes: bytes, file_name: str) -> LoadedDocument:
    ext = extension_of(file_name)
    name = file_name or "file"

    if ext in {".xlsx", ".xls"}:
        df = _read_excel_safe(file_bytes, name)
        return LoadedDocument(kind="table", file_name=name, dataframe=df)

    if ext == ".csv":
        df = _read_csv_flexible(file_bytes)
        return LoadedDocument(kind="table", file_name=name, dataframe=df)

    if ext == ".tsv":
        df = pd.read_csv(io.BytesIO(file_bytes), sep="\t", header=None, dtype=str)
        return LoadedDocument(kind="table", file_name=name, dataframe=df)

    if ext in {".txt", ".text", ".md"}:
        text = file_bytes.decode("utf-8", errors="replace")
        df = _try_text_as_table(text)
        if df is not None and len(df.columns) >= 2:
            return LoadedDocument(kind="table", file_name=name, dataframe=df, text=text)
        return LoadedDocument(kind="text", file_name=name, text=text)

    if ext == ".docx":
        text = _read_docx(file_bytes)
        df = _try_text_as_table(text)
        if df is not None and len(df.columns) >= 2:
            return LoadedDocument(kind="table", file_name=name, dataframe=df, text=text)
        return LoadedDocument(kind="text", file_name=name, text=text)

    if ext == ".doc":
        raise RuntimeError(
            "Формат .doc (старый Word) не поддерживается. "
            "Сохраните файл как .docx или .xlsx."
        )

    if ext == ".pdf":
        text = _read_pdf(file_bytes)
        df = _try_text_as_table(text)
        if df is not None and len(df.columns) >= 2:
            return LoadedDocument(kind="table", file_name=name, dataframe=df, text=text)
        return LoadedDocument(kind="text", file_name=name, text=text)

    if ext in IMAGE_EXTENSIONS:
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".gif": "image/gif",
        }.get(ext, "image/jpeg")
        return LoadedDocument(
            kind="image",
            file_name=name,
            image_bytes=file_bytes,
            image_mime=mime,
        )

    raise RuntimeError(
        f"Формат «{ext or file_name}» не поддерживается. "
        "Допустимо: xlsx, xls, csv, txt, docx, pdf, jpg, png."
    )


def _read_excel_safe(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """Надёжное чтение xls/xlsx; при ошибке xlrd — понятное сообщение."""
    try:
        return read_table(file_bytes, file_name, header=None)
    except Exception as e:
        msg = str(e)
        if "xlrd" in msg.lower() or file_name.lower().endswith(".xls"):
            raise RuntimeError(
                "Не удалось открыть .xls. Установите xlrd>=2.0.1 на сервере "
                "или сохраните файл как .xlsx в Excel/LibreOffice."
            ) from e
        raise RuntimeError(f"Ошибка чтения Excel: {e}") from e


def _read_csv_flexible(file_bytes: bytes) -> pd.DataFrame:
    for sep in (";", ",", "\t", "|"):
        try:
            df = pd.read_csv(
                io.BytesIO(file_bytes),
                sep=sep,
                header=None,
                dtype=str,
                engine="python",
            )
            if df.shape[1] >= 2:
                return df
        except Exception:
            continue
    return pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=str, engine="python")


def _try_text_as_table(text: str) -> pd.DataFrame | None:
    """Пытается разобрать текст как CSV/TSV таблицу."""
    if not text or not text.strip():
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    for sep in ("\t", ";", ",", "|"):
        rows = [ln.split(sep) for ln in lines[:50]]
        widths = {len(r) for r in rows}
        if len(widths) == 1 and list(widths)[0] >= 2:
            try:
                return pd.read_csv(
                    io.StringIO("\n".join(lines)),
                    sep=sep,
                    header=None,
                    dtype=str,
                    engine="python",
                )
            except Exception:
                continue
    return None


def _read_docx(file_bytes: bytes) -> str:
    try:
        from docx import Document
    except ImportError as e:
        raise RuntimeError("Для Word (.docx) установите python-docx") from e
    doc = Document(io.BytesIO(file_bytes))
    parts: list[str] = []
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text.strip() for cell in row.cells))
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text.strip())
    return "\n".join(parts)


def _read_pdf(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("Для PDF установите pypdf") from e
    reader = PdfReader(io.BytesIO(file_bytes))
    parts: list[str] = []
    for page in reader.pages[:30]:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t)
    if not parts:
        raise RuntimeError(
            "PDF не содержит текстового слоя (похоже на скан). "
            "Пришлите скриншот страницы (JPG/PNG) — распознаем через Grok Vision."
        )
    return "\n".join(parts)
