"""Database service layer with atomic transactions."""
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
        telegram_id = int(telegram_id)
        user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
        if not user:
            user = models.User(telegram_id=telegram_id)
            db.add(user)
            db.flush()

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
        db.flush()

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
            db.add_all(db_items)

        db.commit()
        db.refresh(upload_record)
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
        return (
            db.query(models.PriceUpload)
            .options(joinedload(models.PriceUpload.user), joinedload(models.PriceUpload.items))
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


    LAST_PARAMS_NAME = "⭐ Последние"

    @staticmethod
    def save_last_params(db: Session, telegram_id: int, data: dict) -> models.UserPreset:
        """Обновить или создать пресет «Последние» — параметры прошлого расчёта."""
        user = MarginatorDBService.get_or_create_user(db, telegram_id)
        preset = (
            db.query(models.UserPreset)
            .filter(
                models.UserPreset.user_id == user.id,
                models.UserPreset.name == MarginatorDBService.LAST_PARAMS_NAME,
            )
            .first()
        )
        fields = dict(
            calc_mode=data.get("calc_mode", "marketplace"),
            commission_percent=float(data.get("commission_percent", 15) or 15),
            logistics_cost=float(data.get("logistics_cost", 120) or 120),
            packaging_cost=float(data.get("packaging_cost", 30) or 30),
            tax_rate_percent=float(data.get("tax_rate_percent", 6) or 6),
            tax_mode=data.get("tax_mode", "usn_6"),
            freight_cost=float(data.get("freight_cost", 0) or 0),
            manager_bonus_percent=float(data.get("manager_bonus_percent", 0) or 0),
            is_vat_included=bool(data.get("is_vat_included", True)),
            vat_rate_percent=float(data.get("vat_rate_percent", 20) or 20),
            target_margin_percent=(
                float(data["target_margin_percent"])
                if data.get("target_margin_percent") is not None
                else None
            ),
        )
        if preset is None:
            preset = models.UserPreset(user_id=user.id, name=MarginatorDBService.LAST_PARAMS_NAME, **fields)
            db.add(preset)
        else:
            for k, v in fields.items():
                setattr(preset, k, v)
        db.commit()
        db.refresh(preset)
        return preset

    @staticmethod
    def get_last_params(db: Session, telegram_id: int) -> models.UserPreset | None:
        user = db.query(models.User).filter(models.User.telegram_id == int(telegram_id)).first()
        if not user:
            return None
        return (
            db.query(models.UserPreset)
            .filter(
                models.UserPreset.user_id == user.id,
                models.UserPreset.name == MarginatorDBService.LAST_PARAMS_NAME,
            )
            .first()
        )

    @staticmethod
    def save_preset(db: Session, telegram_id: int, name: str, data: dict) -> models.UserPreset:
        user = MarginatorDBService.get_or_create_user(db, telegram_id)
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
            tax_mode=data.get("tax_mode", "usn_6"),
            freight_cost=float(data.get("freight_cost", 0) or 0),
            manager_bonus_percent=float(data.get("manager_bonus_percent", 0) or 0),
            is_vat_included=bool(data.get("is_vat_included", True)),
            vat_rate_percent=float(data.get("vat_rate_percent", 20) or 20),
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


    @staticmethod
    def touch_user(
        db: Session,
        telegram_id: int,
        *,
        username: str | None = None,
        full_name: str | None = None,
        is_new_out: list | None = None,
    ) -> models.User:
        """Создать/обновить пользователя. is_new_out.append(True) если новый."""
        from datetime import datetime
        user = db.query(models.User).filter(models.User.telegram_id == int(telegram_id)).first()
        is_new = user is None
        if is_new:
            user = models.User(telegram_id=int(telegram_id))
            db.add(user)
        user.is_blocked = False
        user.last_seen_at = datetime.utcnow()
        if username is not None:
            user.username = (username or "")[:128] or None
        if full_name is not None:
            user.full_name = (full_name or "")[:256] or None
        db.commit()
        db.refresh(user)
        if is_new_out is not None:
            is_new_out.append(is_new)
        return user

    @staticmethod
    def mark_user_blocked(db: Session, telegram_id: int) -> bool:
        from datetime import datetime
        user = db.query(models.User).filter(models.User.telegram_id == int(telegram_id)).first()
        if not user:
            return False
        user.is_blocked = True
        user.last_seen_at = datetime.utcnow()
        db.commit()
        return True

    @staticmethod
    def list_broadcast_ids(db: Session) -> list[int]:
        rows = (
            db.query(models.User.telegram_id)
            .filter(models.User.is_blocked.is_(False))
            .all()
        )
        return [int(r[0]) for r in rows]

    @staticmethod
    def set_user_rating(db: Session, telegram_id: int, score: int) -> None:
        from datetime import datetime
        user = db.query(models.User).filter(models.User.telegram_id == int(telegram_id)).first()
        if not user:
            user = models.User(telegram_id=int(telegram_id))
            db.add(user)
        user.last_rating = int(score)
        user.last_seen_at = datetime.utcnow()
        db.commit()

    @staticmethod
    def user_stats(db: Session) -> dict:
        total = db.query(models.User).count()
        active = db.query(models.User).filter(models.User.is_blocked.is_(False)).count()
        blocked = db.query(models.User).filter(models.User.is_blocked.is_(True)).count()
        rated = db.query(models.User).filter(models.User.last_rating.isnot(None)).count()
        return {"total": total, "active": active, "blocked": blocked, "rated": rated}
