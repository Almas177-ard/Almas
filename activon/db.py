"""Database session and bootstrap. Never store upstream credentials in plaintext."""
from __future__ import annotations

import json
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet

from .config import config
from .models import Base, CatalogItem, Setting, User

url = config.sqlalchemy_url or ("sqlite:///./activon-demo.sqlite" if config.demo else "")
engine = create_engine(url, pool_pre_ping=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

DEFAULTS = {
    "brand_name": "Activon",
    "partner_base_url": "https://ggsoma.store/api/partner/v1",
    "hamyon_base_url": "https://hamyon-api.uz",
    "hamyon_shop_id": "",
    "exchange_rate": "12800",  # admin must verify current UZS/USD rate before production checkout
    "markup_percent": "25",
    "support_url": "https://t.me/",
    "hero_image": "/static/img/activon-hero.png",
    "logo_image": "/static/img/activon-logo.svg",
    "checkout_enabled": "0",  # must be deliberately enabled by the operator
    "maintenance": "0",
}
SECRET_KEYS = {"partner_api_key", "hamyon_shop_key"}


def _fernet() -> Fernet:
    if config.demo and not config.encryption_key:
        import base64
        import hashlib
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"activon-demo-only-key-not-for-production").digest()))
    return Fernet(config.encryption_key.encode())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(text: str | None) -> str:
    return _fernet().decrypt(text.encode("ascii")).decode("utf-8") if text else ""


def get_setting(db, key: str) -> str:
    obj = db.get(Setting, key)
    value = obj.value if obj else DEFAULTS.get(key, "")
    return decrypt(value) if key in SECRET_KEYS and value else value


def set_setting(db, key: str, value: str) -> None:
    obj = db.get(Setting, key)
    value = encrypt(value) if key in SECRET_KEYS and value else value
    if obj:
        obj.value = value
    else:
        db.add(Setting(key=key, value=value))


@contextmanager
def session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(engine)
    with session() as db:
        if not db.get(Setting, "invoice_lock"):
            db.add(Setting(key="invoice_lock", value="0"))
    if not config.demo:
        return
    from .seed import PRODUCTS, LOCAL_COPY
    with session() as db:
        if not db.scalar(select(User).where(User.telegram_id == 900000001)):
            db.add(User(telegram_id=900000001, first_name="Aziza", username="activon_demo", language="uz", role="ADMIN", balance_som=350000))
        for item in PRODUCTS:
            if not db.scalar(select(CatalogItem).where(CatalogItem.slug == item["slug"])):
                copy = LOCAL_COPY[item["slug"]]
                db.add(CatalogItem(slug=item["slug"], name=item["name"], provider_key=item["provider"]["key"],
                    data_json=json.dumps(item), image_path=f'/static/img/products/{item["provider"]["key"]}.svg',
                    sort_order=item["sortOrder"], custom_price_som=None if item["slug"] == "gemini-pro-monthly" else item["demoPrice"],
                    title_uz=copy["uz"][0], title_ru=copy["ru"][0], title_en=copy["en"][0],
                    desc_uz=copy["uz"][1], desc_ru=copy["ru"][1], desc_en=copy["en"][1]))
