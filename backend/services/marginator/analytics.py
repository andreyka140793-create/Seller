"""Analytics, summary and term glossary for reports."""
from __future__ import annotations

import re
import pandas as pd
from pydantic import BaseModel


def _escape_md(text: str) -> str:
    """Экранирует спецсимволы легаси-Markdown Telegram."""
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


# Краткие определения (в духе юнит-экономики / статьи Т-Банка о марже)
TERMS: dict[str, str] = {
    "Выручка": "Сколько денег приносит товар при продаже (цена продажи × количество).",
    "Переменные расходы": "Затраты на единицу: закуп + комиссия МП + логистика + упаковка и т.п.",
    "Маржа, ₽": "Выручка минус переменные расходы. Сколько «остаётся» до налога.",
    "Маржинальность %": "Маржа ÷ выручка × 100%. Какая доля выручки остаётся после переменных.",
    "Наценка %": "Маржа ÷ переменные × 100%. На сколько продажа выше затрат (не путать с маржинальностью).",
    "Чистая прибыль": "Маржа минус налог. Оценка «в карман» после налога с этой модели.",
    "Рентабельность чистая %": "Чистая прибыль ÷ выручка × 100%.",
    "ROI %": "Чистая прибыль ÷ себестоимость (закуп) × 100%. Отдача на вложенные в товар деньги.",
    "Зона риска": "Маржинальность ниже 5% — мало запаса на возвраты, акции и ошибки в тарифах.",
}


GLOSSARY_TEXT = """📖 *Как читать цифры Маржинатора*

*Выручка* — деньги с продажи.
*Переменные* — закуп, комиссия, логистика, упаковка…
*Маржа, ₽* = выручка − переменные (до налога).
*Маржинальность %* = маржа ÷ выручка — «доля» с продажи.
*Наценка %* = маржа ÷ переменные — не то же самое, что маржинальность.
*Чистая прибыль* ≈ маржа − налог.
*ROI %* — отдача на закуп (чистая ÷ себестоимость).

⚠️ *Зона риска* — маржинальность *ниже 5%*: мало запаса на скидки и сбои.

Цвета в Excel:
🟢 ≥ 20%  ·  🟡 5–20%  ·  🔴 < 5%
"""


class BatchSummary(BaseModel):
    total_items: int
    total_revenue: float
    total_margin: float
    total_profit: float
    avg_margin_pct: float
    avg_net_margin_pct: float
    avg_roi_pct: float = 0.0
    profitable_count: int = 0
    top_profitable: list[dict]
    unprofitable_count: int
    risk_items: list[dict]
    file_label: str = ""
    risk_threshold: float = 5.0


class AnalyticsService:
    @staticmethod
    def generate_summary(df_results: pd.DataFrame, file_label: str = "", risk_threshold: float = 5.0) -> BatchSummary:
        total_items = len(df_results)
        total_revenue = float(df_results["Выручка, ₽"].sum()) if total_items else 0.0
        total_profit = float(df_results["Чистая прибыль, ₽"].sum()) if total_items else 0.0
        total_margin = (
            float(df_results["Маржа, ₽"].sum())
            if "Маржа, ₽" in df_results.columns and total_items
            else total_profit
        )

        avg_margin_pct = (total_margin / total_revenue * 100.0) if total_revenue > 0 else 0.0
        avg_net_margin_pct = (total_profit / total_revenue * 100.0) if total_revenue > 0 else 0.0
        avg_roi = (
            float(df_results["ROI %"].mean())
            if total_items and "ROI %" in df_results.columns
            else 0.0
        )

        margin_col = (
            "Маржинальность %"
            if "Маржинальность %" in df_results.columns
            else "Рентабельность чистая %"
        )

        top_df = df_results.sort_values(by="Чистая прибыль, ₽", ascending=False).head(3)
        top_profitable = [
            {
                "name": row["Товар"],
                "profit": row["Чистая прибыль, ₽"],
                "margin": row.get(margin_col, 0),
            }
            for _, row in top_df.iterrows()
        ]

        if margin_col in df_results.columns:
            risk_df = df_results[df_results[margin_col] < risk_threshold]
            profitable_count = int((df_results[margin_col] >= risk_threshold).sum())
        else:
            risk_df = df_results[df_results["Чистая прибыль, ₽"] < 0]
            profitable_count = int((df_results["Чистая прибыль, ₽"] >= 0).sum())

        unprofitable_count = len(risk_df)
        risk_items = [
            {"name": row["Товар"], "margin": row.get(margin_col, 0)}
            for _, row in risk_df.head(3).iterrows()
        ]

        return BatchSummary(
            total_items=total_items,
            total_revenue=round(total_revenue, 2),
            total_margin=round(total_margin, 2),
            total_profit=round(total_profit, 2),
            avg_margin_pct=round(avg_margin_pct, 2),
            avg_net_margin_pct=round(avg_net_margin_pct, 2),
            avg_roi_pct=round(avg_roi, 2),
            profitable_count=profitable_count,
            top_profitable=top_profitable,
            unprofitable_count=unprofitable_count,
            risk_items=risk_items,
            file_label=file_label or "",
            risk_threshold=float(risk_threshold),
        )

    @staticmethod
    def format_summary_message(summary: BatchSummary) -> str:
        head = f" (`{_escape_md(summary.file_label)}`)" if summary.file_label else ""
        text = (
            f"📊 **Итог расчёта**{head}\n"
            f"───────────────────\n"
            f"• **Позиций:** `{summary.total_items}`\n"
            f"• **В плюсе** (маржа ≥ порога): `{summary.profitable_count}`\n"
            f"• **Зона риска** (ниже порога): `{summary.unprofitable_count}`\n"
            f"• **Выручка** (сумма цен продаж): `{summary.total_revenue:,.0f} ₽`\n"
            f"• **Маржа до налога** (выручка − переменные): `{summary.total_margin:,.0f} ₽`\n"
            f"• **Чистая прибыль** (после налога): `{summary.total_profit:,.0f} ₽`\n"
            f"• **Ср. маржинальность** (маржа/выручка): `{summary.avg_margin_pct}%`\n"
            f"• **Ср. рентаб. чистая** (чистая/выручка): `{summary.avg_net_margin_pct}%`\n"
            f"• **Ср. ROI** (чистая/закуп): `{summary.avg_roi_pct}%`\n\n"
        )
        text += "🏆 **Топ-3 по чистой прибыли:**\n"
        for i, item in enumerate(summary.top_profitable, 1):
            text += (
                f"{i}. {_escape_md(item['name'])} — "
                f"`{item['profit']:,.0f} ₽` (марж. `{item['margin']}%`)\n"
            )
        if summary.unprofitable_count > 0:
            text += "\n⚠️ **Примеры в зоне риска** (мало запаса на скидки/возвраты):\n"
            for item in summary.risk_items:
                text += f"• {_escape_md(item['name'])} — `{item['margin']}%`\n"

        text += (
            "\n📌 **Коротко о терминах**\n"
            "• *Маржа ₽* ≠ *маржинальность %* ≠ *наценка %*\n"
            "• Маржинальность — доля от **выручки**; наценка — надбавка к **затратам**\n"
            "• В Excel: лист «Справка»; красный = ниже порога риска\n"
            "• Подробнее: команда /terms"
        )
        return text

    @staticmethod
    def glossary_message() -> str:
        return GLOSSARY_TEXT
