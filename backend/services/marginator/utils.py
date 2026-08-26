"""Utility functions."""
import re
import pandas as pd


def clean_numeric_value(value, allow_negative: bool = False) -> float:
    """Convert arbitrary cell data to float."""
    if value is None or pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if allow_negative else max(0.0, float(value))

    cleaned = (
        str(value)
        .replace(" ", "")
        .replace(" ", "")
        .replace("₽", "")
        .replace("$", "")
        .replace(",", ".")
        .strip()
    )
    try:
        val = float(cleaned)
        return val if allow_negative else max(0.0, val)
    except ValueError:
        return 0.0


def sanitize_filename(name: str) -> str:
    """Sanitize filename for safe filesystem usage."""
    name = name.split("/")[-1].split("\")[-1]
    return re.sub(r"[^a-zA-Z0-9._-]", "", name)[:50]
