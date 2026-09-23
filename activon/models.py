"""Money in customer-facing tables is integer UZS; upstream USD is internal only."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now() -> datetime:
    # Store naive UTC consistently for MySQL and SQLite.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, nullable=False, unique=True, index=True)
    first_name = Column(String(100), nullable=False)
    username = Column(String(64), nullable=True)
    photo_url = Column(String(1024), nullable=True)
    avatar_path = Column(String(255), nullable=True)  # optional customer-uploaded profile photo
    language = Column(String(2), nullable=False, default="uz")
    role = Column(String(20), nullable=False, default="CUSTOMER")
    blocked = Column(Boolean, nullable=False, default=False)
    balance_som = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime, default=now, nullable=False)
    updated_at = Column(DateTime, default=now, onupdate=now, nullable=False)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)


class TextOverride(Base):
    __tablename__ = "text_overrides"
    key = Column(String(120), primary_key=True)
    lang = Column(String(2), primary_key=True)
    value = Column(Text, nullable=False)


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    id = Column(Integer, primary_key=True)
    slug = Column(String(160), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    provider_key = Column(String(100), nullable=False)
    data_json = Column(Text, nullable=False)
    listed = Column(Boolean, nullable=False, default=True)
    visible = Column(Boolean, nullable=False, default=True)
    custom_price_som = Column(BigInteger, nullable=True)
    markup_percent = Column(Numeric(8, 2), nullable=True)
    title_uz = Column(String(255), nullable=True)
    title_ru = Column(String(255), nullable=True)
    title_en = Column(String(255), nullable=True)
    desc_uz = Column(Text, nullable=True)
    desc_ru = Column(Text, nullable=True)
    desc_en = Column(Text, nullable=True)
    image_path = Column(String(255), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=now, onupdate=now, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    code = Column(String(36), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_slug = Column(String(160), nullable=False)
    product_name = Column(String(255), nullable=False)
    image_path = Column(String(255), nullable=True)
    delivery_type = Column(String(24), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_som = Column(BigInteger, nullable=False)
    total_som = Column(BigInteger, nullable=False)
    # Internal snapshot for margin reporting; NEVER rendered as a currency in the UI.
    upstream_unit_usd = Column(Numeric(12, 2), nullable=False)
    rate_snapshot = Column(Integer, nullable=False)
    method = Column(String(10), nullable=False)  # CARD or WALLET
    status = Column(String(24), nullable=False)
    external_order_id = Column(String(64), nullable=False, unique=True)
    upstream_order_code = Column(String(64), nullable=True)
    delivery_enc = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    processing_at = Column(DateTime, nullable=True)
    retry_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(80), nullable=True)
    last_error_request_id = Column(String(96), nullable=True)
    created_at = Column(DateTime, default=now, nullable=False, index=True)
    updated_at = Column(DateTime, default=now, onupdate=now, nullable=False)
    __table_args__ = (Index("ix_orders_status_retry", "status", "retry_at"),)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(String(36), primary_key=True)  # sent as order_id to Hamyon
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, unique=True)
    purpose = Column(String(10), nullable=False)  # PRODUCT or TOPUP
    amount_som = Column(BigInteger, nullable=False)
    status = Column(String(24), nullable=False, index=True)
    provider_payment_id = Column(String(128), nullable=True, unique=True)
    shop_id_snapshot = Column(String(128), nullable=True)
    signing_key_enc = Column(Text, nullable=True)  # allows rotation during an open payment
    card = Column(String(36), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now, nullable=False)
    updated_at = Column(DateTime, default=now, onupdate=now, nullable=False)


class WalletEntry(Base):
    __tablename__ = "wallet_entries"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    payment_id = Column(String(36), ForeignKey("payments.id"), nullable=True, unique=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    kind = Column(String(20), nullable=False)  # TOPUP, PURCHASE, REFUND, ADJUSTMENT
    amount_som = Column(BigInteger, nullable=False)  # signed
    balance_after = Column(BigInteger, nullable=False)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=now, nullable=False)
    __table_args__ = (UniqueConstraint("order_id", "kind", name="uq_wallet_order_kind"),)


class Audit(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(80), nullable=False)
    target = Column(String(180), nullable=False)
    detail = Column(String(400), nullable=True)
    created_at = Column(DateTime, default=now, nullable=False)
