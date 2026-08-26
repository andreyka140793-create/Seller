"""SQLAlchemy models."""
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, ForeignKey, DateTime, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    uploads = relationship("PriceUpload", back_populates="user", cascade="all, delete-orphan")
    presets = relationship("UserPreset", back_populates="user", cascade="all, delete-orphan")


class UserPreset(Base):
    __tablename__ = "user_presets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    calc_mode = Column(String(32), default="marketplace")
    commission_percent = Column(Float, default=15.0)
    logistics_cost = Column(Float, default=120.0)
    packaging_cost = Column(Float, default=30.0)
    tax_rate_percent = Column(Float, default=6.0)
    tax_mode = Column(String(16), default="usn_6")
    freight_cost = Column(Float, default=0.0)
    manager_bonus_percent = Column(Float, default=0.0)
    is_vat_included = Column(Boolean, default=True)
    vat_rate_percent = Column(Float, default=20.0)
    target_margin_percent = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="presets")


class PriceUpload(Base):
    __tablename__ = "price_uploads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    calc_mode = Column(String(32), default="marketplace")
    status = Column(String(32), default="processing")
    total_revenue = Column(Float, default=0.0)
    total_profit = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="uploads")
    items = relationship("AnalyzedItem", back_populates="upload", cascade="all, delete-orphan")


class AnalyzedItem(Base):
    __tablename__ = "analyzed_items"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("price_uploads.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    sku = Column(String(255), nullable=True)
    buy_price = Column(Float, nullable=False)
    est_sell_price = Column(Float, nullable=False)
    net_profit = Column(Float, nullable=False)
    margin_pct = Column(Float, nullable=False, default=0.0)
    roi_pct = Column(Float, nullable=False)
    is_profitable = Column(Boolean, default=False)

    upload = relationship("PriceUpload", back_populates="items")


# Additional indexes for performance
Index("ix_analyzed_items_upload_id", AnalyzedItem.upload_id)
Index("ix_price_uploads_user_id_created", PriceUpload.user_id, PriceUpload.created_at.desc())
