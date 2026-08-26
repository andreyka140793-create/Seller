import pandas as pd
from sqlalchemy.orm import Session, joinedload
import models


class MarginatorDBService:
    @staticmethod
    def save_calculation_results(
        db: Session,
        telegram_id: int,
        filename: str,
        calc_mode: str,
        df_results: pd.DataFrame,
    ) -> models.PriceUpload:
        """Создаёт/находит пользователя, сохраняет загрузку и позиции."""
        telegram_id = int(telegram_id)
        user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
        if not user:
            user = models.User(telegram_id=telegram_id)
            db.add(user)
            db.commit()
            db.refresh(user)

        total_revenue = float(df_results["Выручка, ₽"].sum()) if "Выручка, ₽" in df_results.columns else 0.0
        total_profit = float(df_results["Чистая прибыль, ₽"].sum()) if "Чистая прибыль, ₽" in df_results.columns else 0.0

        upload_record = models.PriceUpload(
            user_id=user.id,
            filename=filename or "price.xlsx",
            calc_mode=calc_mode or "marketplace",
            status="completed",
            total_revenue=round(total_revenue, 2),
            total_profit=round(total_profit, 2),
        )
        db.add(upload_record)
        db.commit()
        db.refresh(upload_record)

        db_items = []
        for _, row in df_results.iterrows():
            try:
                margin_val = float(row.get("Маржинальность %", 0) or 0)
            except Exception:
                margin_val = 0.0
            try:
                buy = float(row.get("Себестоимость, ₽", 0) or 0)
            except Exception:
                buy = 0.0
            try:
                sell = float(row.get("Выручка, ₽", 0) or 0)
            except Exception:
                sell = 0.0
            try:
                profit = float(row.get("Чистая прибыль, ₽", 0) or 0)
            except Exception:
                profit = 0.0
            try:
                roi = float(row.get("ROI %", 0) or 0)
            except Exception:
                roi = 0.0
            title = str(row.get("Товар", "—") or "—")[:2000]
            db_items.append(
                models.AnalyzedItem(
                    upload_id=upload_record.id,
                    title=title,
                    buy_price=buy,
                    est_sell_price=sell,
                    net_profit=profit,
                    margin_pct=margin_val,
                    roi_pct=roi,
                    is_profitable=(margin_val >= 5.0),
                )
            )

        if db_items:
            db.bulk_save_objects(db_items)
            db.commit()

        return upload_record

    @staticmethod
    def get_user_history(db: Session, telegram_id: int, limit: int = 15) -> list:
        telegram_id = int(telegram_id)
        user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
        if not user:
            return []
        return (
            db.query(models.PriceUpload)
            .filter(models.PriceUpload.user_id == user.id)
            .order_by(models.PriceUpload.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_upload_with_items(db: Session, upload_id: int):
        """Загрузка + user + items одним запросом."""
        return (
            db.query(models.PriceUpload)
            .options(
                joinedload(models.PriceUpload.user),
                joinedload(models.PriceUpload.items),
            )
            .filter(models.PriceUpload.id == int(upload_id))
            .first()
        )
