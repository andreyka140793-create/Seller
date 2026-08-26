"""Курс ЦБ РФ через cbr-xml-daily.ru (JSON-зеркало официальных курсов)."""
from __future__ import annotations

import json
import urllib.request
from functools import lru_cache
from time import time

_CACHE: dict = {"ts": 0.0, "data": None}
_TTL = 3600  # 1 час


def fetch_cbr_rates() -> dict[str, float]:
    """
    Возвращает { 'USD': 92.5, 'EUR': ..., 'CNY': ... } — рубли за 1 единицу валюты.
    Учитывает Nominal (для CNY часто 10 юаней).
    """
    now = time()
    if _CACHE["data"] and now - _CACHE["ts"] < _TTL:
        return _CACHE["data"]

    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    req = urllib.request.Request(url, headers={"User-Agent": "MarginatorBot/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    rates: dict[str, float] = {}
    for code, info in (payload.get("Valute") or {}).items():
        try:
            value = float(info["Value"])
            nominal = float(info.get("Nominal") or 1)
            if nominal > 0:
                rates[code] = round(value / nominal, 4)
        except (KeyError, TypeError, ValueError):
            continue

    _CACHE["ts"] = now
    _CACHE["data"] = rates
    return rates


def get_cbr_rate(code: str) -> float | None:
    code = (code or "").upper().strip()
    if code in ("RUB", "RUR"):
        return 1.0
    rates = fetch_cbr_rates()
    return rates.get(code)
