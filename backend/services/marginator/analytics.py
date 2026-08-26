"""Analytics and summary generation."""
import pandas as pd
from pydantic import BaseModel


class BatchSummary(BaseModel):
    total_items: int
    total_revenue: float
    total_margin: float
    total_profit: float
    avg_margin_pct: float
    avg_net_margin_pct: float
    top_profitable: list[dict]
    unprofitable_count: int
    risk_items: list[dict]


class AnalyticsService:
    @staticmethod
    def generate_summary(df_results: pd.DataFrame) -> BatchSummary:
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

        margin_col = "Маржинальность %" if "Маржинальность %" in df_results.columns else "Рентабельность чистая %"

        top_df = df_results.sort_values(by="Чистая прибыль, ₽", ascending=False).head(3)
        top_profitable = [
            {"name": row["Товар"], "profit": row["Чистая прибыль, ₽"], "margin": row.get(margin_col, 0)}
            for _, row in top_df.iterrows()
        ]

        if margin_col in df_results.columns:
            risk_df = df_results[df_results[margin_col] < 5.0]
        else:
            risk_df = df_results[df_results["Чистая прибыль, ₽"] < 0]
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
            top_profitable=top_profitable,
            unprofitable_count=unprofitable_count,
            risk_items=risk_items,
        )

    @staticmethod
    def format_summary_message(summary: BatchSummary) -> str:
        text = (
            f"📊 **Итог по партии**
"
            f"───────────────────
"
            f"• **Товаров:** `{summary.total_items}`
"
            f"• **Выручка:** `{summary.total_revenue:,.2f} ₽`
"
            f"• **Маржа (до налога):** `{summary.total_margin:,.2f} ₽`
"
            f"• **Чистая прибыль:** `{summary.total_profit:,.2f} ₽`
"
            f"• **Маржинальность:** `{summary.avg_margin_pct}%`
"
            f"• **Рентабельность чистая:** `{summary.avg_net_margin_pct}%`

"
        )
        text += "🏆 **Топ-3 по чистой прибыли:**
"
        for i, item in enumerate(summary.top_profitable, 1):
            text += f"{i}. {item['name']} — `{item['profit']:,.2f} ₽` (марж. `{item['margin']}%`)
"
        if summary.unprofitable_count > 0:
            text += f"
⚠️ **Зона риска (маржинальность < 5%):** `{summary.unprofitable_count}` шт.
"
            for item in summary.risk_items:
                text += f"• {item['name']} — `{item['margin']}%`
"
        text += (
            "
_Маржа ₽ = выручка − переменные; "
            "маржинальность = маржа/выручка; "
            "наценка = маржа/переменные; "
            "чистая = маржа − налог._"
        )
        return text
