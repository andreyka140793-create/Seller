from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    uploads = relationship("PriceUpload", back_populates="user")
    presets = relationship("UserPreset", back_populates="user", cascade="all, delete-orphan")


class UserPreset(Base):
    """Сохранённые параметры расчёта пользователя."""
    __tablename__ = "user_presets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    calc_mode = Column(String(32), default="marketplace")  # marketplace | b2b
    # marketplace
    commission_percent = Column(Float, default=15.0)
    logistics_cost = Column(Float, default=120.0)
    packaging_cost = Column(Float, default=30.0)
    tax_rate_percent = Column(Float, default=6.0)
    # b2b
    freight_cost = Column(Float, default=0.0)
    manager_bonus_percent = Column(Float, default=0.0)
    is_vat_included = Column(Boolean, default=True)
    # target
    target_margin_percent = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="presets")


class PriceUpload(Base):
    __tablename__ = "price_uploads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String, nullable=False)
    calc_mode = Column(String, default="marketplace")
    status = Column(String, default="processing")
    total_revenue = Column(Float, default=0.0)
    total_profit = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="uploads")
    items = relationship("AnalyzedItem", back_populates="upload", cascade="all, delete-orphan")


class AnalyzedItem(Base):
    __tablename__ = "analyzed_items"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("price_uploads.id"), nullable=False)
    title = Column(Text, nullable=False)
    sku = Column(String, nullable=True)
    buy_price = Column(Float, nullable=False)
    est_sell_price = Column(Float, nullable=False)
    net_profit = Column(Float, nullable=False)
    margin_pct = Column(Float, nullable=False, default=0.0)
    roi_pct = Column(Float, nullable=False)
    is_profitable = Column(Boolean, default=False)

    upload = relationship("PriceUpload", back_populates="items")
