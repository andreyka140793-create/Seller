"""Price list comparison."""
from __future__ import annotations
import re
from typing import Any
import pandas as pd
from services.marginator.utils import clean_numeric_value


def _norm_key(val: Any) -> str:
    s = str(val or "").strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s


def _find_col(df: pd.DataFrame, kinds: tuple[str, ...]) -> str | None:
    best, score = None, 0
    for c in df.columns:
        n = str(c).strip().lower().replace("ё", "е")
        sc = sum(10 for k in kinds if k in n)
        if sc > score:
            score, best = sc, str(c)
    return best if score >= 10 else None


def build_price_index(df: pd.DataFrame) -> dict[str, dict]:
    name_col = _find_col(df, ("наимен", "назван", "товар", "номенклат", "product", "name"))
    sku_col = _find_col(df, ("артикул", "sku", "код товар", "article"))
    price_col = _find_col(df, ("цена", "price", "руб", "р.", "себестоим", "закуп", "cost", "-i-", "-ii-", "-iii-"))
    if price_col and str(price_col).strip().lower() in ("ед", "ед.", "ед.изм"):
        price_col = None
        for c in df.columns:
            cn = str(c).lower()
            if any(x in cn for x in ("руб", "цена", "price", "₽")) and "ед" not in cn[:3]:
                price_col = str(c)
                break

    index: dict[str, dict] = {}
    if not name_col and not sku_col:
        return index
    if not price_col:
        return index

    for _, row in df.iterrows():
        name = str(row.get(name_col, "") or "").strip() if name_col else ""
        sku = str(row.get(sku_col, "") or "").strip() if sku_col else ""
        price = clean_numeric_value(row.get(price_col))
        if price <= 0:
            continue
        key = _norm_key(sku) if sku and sku.lower() not in ("nan", "none", "") else _norm_key(name)
        if not key or key in ("nan", "none"):
            continue
        if key not in index or price < index[key]["price"]:
            index[key] = {"name": name or sku or key, "sku": sku, "price": price}
    return index


def compare_price_lists(df_a: pd.DataFrame, df_b: pd.DataFrame, label_a: str = "A", label_b: str = "B") -> pd.DataFrame:
    ia = build_price_index(df_a)
    ib = build_price_index(df_b)
    keys = sorted(set(ia) | set(ib))
    rows = []
    for k in keys:
        a = ia.get(k)
        b = ib.get(k)
        if a and b:
            delta = b["price"] - a["price"]
            delta_pct = (delta / a["price"] * 100.0) if a["price"] else 0.0
            status = "есть в обоих"
            if delta_pct <= -5:
                verdict = "B выгоднее"
            elif delta_pct >= 5:
                verdict = "A выгоднее"
            else:
                verdict = "почти одинаково"
            rows.append({
                "Товар": a["name"] or b["name"],
                "Артикул": a.get("sku") or b.get("sku") or "",
                f"Цена {label_a}, ₽": a["price"],
                f"Цена {label_b}, ₽": b["price"],
                "Разница, ₽": round(delta, 2),
                "Разница %": round(delta_pct, 2),
                "Статус": status,
                "Вывод": verdict,
            })
        elif a:
            rows.append({
                "Товар": a["name"], "Артикул": a.get("sku") or "",
                f"Цена {label_a}, ₽": a["price"], f"Цена {label_b}, ₽": None,
                "Разница, ₽": None, "Разница %": None,
                "Статус": "только в A", "Вывод": "нет в B",
            })
        else:
            rows.append({
                "Товар": b["name"], "Артикул": b.get("sku") or "",
                f"Цена {label_a}, ₽": None, f"Цена {label_b}, ₽": b["price"],
                "Разница, ₽": None, "Разница %": None,
                "Статус": "только в B", "Вывод": "нет в A",
            })
    return pd.DataFrame(rows)
