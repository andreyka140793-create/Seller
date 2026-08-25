import pandas as pd
from sqlalchemy.orm import Session
import models

class MarginatorDBService:
    @staticmethod
    def save_calculation_results(
        db: Session,
        telegram_id: int,
        filename: str,
        calc_mode: str,
        df_results: pd.DataFrame
    ) -> models.PriceUpload:
        """
        Создает или находит пользователя, фиксирует загрузку и массово сохраняет товары.
        """
        # 1. Поиск или создание пользователя
        user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
        if not user:
            user = models.User(telegram_id=telegram_id)
            db.add(user)
            db.commit()
            db.refresh(user)

        # 2. Создание записи о загрузке
        total_revenue = float(df_results["Выручка, ₽"].sum())
        total_profit = float(df_results["Чистая прибыль, ₽"].sum())

        upload_record = models.PriceUpload(
            user_id=user.id,
            filename=filename,
            calc_mode=calc_mode,
            status="completed",
            total_revenue=round(total_revenue, 2),
            total_profit=round(total_profit, 2)
        )
        db.add(upload_record)
        db.commit()
        db.refresh(upload_record)

        # 3. Подготовка и пакетное сохранение расчитанных позиций
        db_items = []
        for _, row in df_results.iterrows():
            margin_val = float(row["Маржинальность %"])
            db_items.append(
                models.AnalyzedItem(
                    upload_id=upload_record.id,
                    title=str(row["Товар"]),
                    buy_price=float(row["Себестоимость, ₽"]),
                    est_sell_price=float(row["Выручка, ₽"]),
                    net_profit=float(row["Чистая прибыль, ₽"]),
                    margin_pct=margin_val,
                    roi_pct=float(row["ROI %"]),
                    is_profitable=(margin_val >= 5.0)
                )
            )

        db.bulk_save_objects(db_items)
        db.commit()

        return upload_record

    @staticmethod
    def get_user_history(db: Session, telegram_id: int, limit: int = 5) -> list[models.PriceUpload]:
        """Возвращает последние N загрузок пользователя."""
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
    def get_upload_with_items(db: Session, upload_id: int) -> models.PriceUpload | None:
        """Получает загрузку со всеми расчитанными позициями."""
        return db.query(models.PriceUpload).filter(models.PriceUpload.id == upload_id).first()
