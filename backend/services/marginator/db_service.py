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
                margin_val = float(row.get("Маржинальность %", row.get("Рентабельность чистая %", 0)) or 0)
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


    @staticmethod
    def get_or_create_user(db: Session, telegram_id: int) -> models.User:
        telegram_id = int(telegram_id)
        user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
        if not user:
            user = models.User(telegram_id=telegram_id)
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    @staticmethod
    def list_presets(db: Session, telegram_id: int) -> list:
        user = db.query(models.User).filter(models.User.telegram_id == int(telegram_id)).first()
        if not user:
            return []
        return (
            db.query(models.UserPreset)
            .filter(models.UserPreset.user_id == user.id)
            .order_by(models.UserPreset.created_at.desc())
            .limit(8)
            .all()
        )

    @staticmethod
    def save_preset(db: Session, telegram_id: int, name: str, data: dict) -> models.UserPreset:
        user = MarginatorDBService.get_or_create_user(db, telegram_id)
        # limit 8 presets — удаляем самые старые
        existing = (
            db.query(models.UserPreset)
            .filter(models.UserPreset.user_id == user.id)
            .order_by(models.UserPreset.created_at.desc())
            .all()
        )
        if len(existing) >= 8:
            for old in existing[7:]:
                db.delete(old)
        preset = models.UserPreset(
            user_id=user.id,
            name=(name or "Пресет")[:64],
            calc_mode=data.get("calc_mode", "marketplace"),
            commission_percent=float(data.get("commission_percent", 15) or 15),
            logistics_cost=float(data.get("logistics_cost", 120) or 120),
            packaging_cost=float(data.get("packaging_cost", 30) or 30),
            tax_rate_percent=float(data.get("tax_rate_percent", 6) or 6),
            freight_cost=float(data.get("freight_cost", 0) or 0),
            manager_bonus_percent=float(data.get("manager_bonus_percent", 0) or 0),
            is_vat_included=bool(data.get("is_vat_included", True)),
            target_margin_percent=(
                float(data["target_margin_percent"])
                if data.get("target_margin_percent") is not None
                else None
            ),
        )
        db.add(preset)
        db.commit()
        db.refresh(preset)
        return preset

    @staticmethod
    def get_preset(db: Session, telegram_id: int, preset_id: int):
        user = db.query(models.User).filter(models.User.telegram_id == int(telegram_id)).first()
        if not user:
            return None
        return (
            db.query(models.UserPreset)
            .filter(models.UserPreset.id == preset_id, models.UserPreset.user_id == user.id)
            .first()
        )

    @staticmethod
    def delete_preset(db: Session, telegram_id: int, preset_id: int) -> bool:
        preset = MarginatorDBService.get_preset(db, telegram_id, preset_id)
        if not preset:
            return False
        db.delete(preset)
        db.commit()
        return True
