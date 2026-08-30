"""Application configuration."""
import os
from typing import Literal


class PurchasingConfig:
    # Profitability thresholds
    MIN_ROI_PCT: float = 35.0
    MIN_NET_PROFIT_RUB: float = 200.0
    MIN_SUPPLIER_STOCK: int = 50

    # Default marketplace params
    DEFAULT_LOGISTICS_RUB: float = 120.0
    DEFAULT_PACKAGING_RUB: float = 30.0
    DEFAULT_MP_COMMISSION_PCT: float = 15.0
    DEFAULT_TAX_PCT: float = 6.0
    DEFAULT_MARKUP_PCT: float = 100.0
    DEFAULT_RISK_MARGIN_PCT: float = 5.0  # зона риска: маржинальность ниже этого %

    # Tax modes
    TAX_MODE: Literal["usn_6", "usn_15", "osno_20"] = os.getenv("TAX_MODE", "usn_6")


def get_admin_ids() -> set[int]:
    """ID админов из ADMIN_TELEGRAM_IDS (через запятую) или ADMIN_TELEGRAM_ID."""
    raw = os.getenv("ADMIN_TELEGRAM_IDS") or os.getenv("ADMIN_TELEGRAM_ID") or ""
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids
