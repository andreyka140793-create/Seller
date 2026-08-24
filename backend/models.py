import datetime
from sqlalchemy import Column, Integer, String, BigInteger, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    uploads = relationship("PriceUpload", back_populates="owner")

class PriceUpload(Base):
    __tablename__ = "price_uploads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String, nullable=False)
    status = Column(String, default="processing")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="uploads")
    items = relationship("AnalyzedItem", back_populates="upload")

class AnalyzedItem(Base):
    __tablename__ = "analyzed_items"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("price_uploads.id"), nullable=False)
    title = Column(String, nullable=False)
    sku = Column(String, nullable=True)
    buy_price = Column(Float, nullable=False)
    est_sell_price = Column(Float, nullable=True)
    net_profit = Column(Float, nullable=True)
    roi_pct = Column(Float, nullable=True)
    is_profitable = Column(Boolean, default=False)

    upload = relationship("PriceUpload", back_populates="items")
