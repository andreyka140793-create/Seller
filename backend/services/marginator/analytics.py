import pandas as pd
from pydantic import BaseModel

class BatchSummary(BaseModel):
    total_items: int
    total_revenue: float
    total_profit: float
    avg_margin: float
    top_profitable: list[dict]
    unprofitable_count: int
    risk_items: list[dict]

class AnalyticsService:
    @staticmethod
    def generate_summary(df_results: pd.DataFrame) -> BatchSummary:
        total_items = len(df_results)
        total_revenue = float(df_results["Выручка, ₽"].sum())
        total_profit = float(df_results["Чистая прибыль, ₽"].sum())
        
        # Средняя маржа по всей партии
        avg_margin = (total_profit / total_revenue * 100.0) if total_revenue > 0 else 0.0

        # Топ-3 самых прибыльных товара
        top_df = df_results.sort_values(by="Чистая прибыль, ₽", ascending=False).head(3)
        top_profitable = [
            {"name": row["Товар"], "profit": row["Чистая прибыль, ₽"], "margin": row["Маржинальность %"]}
            for _, row in top_df.iterrows()
        ]

        # Товары с отрицательной или низкой маржой (< 5%)
        risk_df = df_results[df_results["Маржинальность %"] < 5.0]
        unprofitable_count = len(risk_df)
        
        risk_items = [
            {"name": row["Товар"], "margin": row["Маржинальность %"]}
            for _, row in risk_df.head(3).iterrows()
        ]

        return BatchSummary(
            total_items=total_items,
            total_revenue=round(total_revenue, 2),
            total_profit=round(total_profit, 2),
            avg_margin=round(avg_margin, 2),
            top_profitable=top_profitable,
            unprofitable_count=unprofitable_count,
            risk_items=risk_items
        )

    @staticmethod
    def format_summary_message(summary: BatchSummary) -> str:
        text = (
            f"📊 **Итоговый отчет по партии**\n"
            f"───────────────────\n"
            f"• **Всего товаров:** `{summary.total_items}` шт.\n"
            f"• **Общая выручка:** `{summary.total_revenue:,.2f} ₽`\n"
            f"• **Чистая прибыль:** `{summary.total_profit:,.2f} ₽`\n"
            f"• **Средняя маржа партии:** `{summary.avg_margin}%`\n\n"
        )

        text += "🏆 **Топ-3 прибыльных товара:**\n"
        for i, item in enumerate(summary.top_profitable, 1):
            text += f"{i}. {item['name']} — `{item['profit']:,.2f} ₽` (Маржа: `{item['margin']}%`)\n"

        if summary.unprofitable_count > 0:
            text += f"\n⚠️ **Товары в зоне риска (< 5% маржи):** `{summary.unprofitable_count}` шт.\n"
            for item in summary.risk_items:
                text += f"• {item['name']} — Маржа: `{item['margin']}%`\n"

        return text
