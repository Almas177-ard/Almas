"""Catalog caching and UZS-only resale pricing. No inventory secrets are cached."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from sqlalchemy import select

from .db import get_setting, session
from .models import CatalogItem
from .providers import PartnerClient, ServiceError

SUPPORTED = {"LINK", "COUPON", "READY_ACCOUNT"}


def safe_decimal(raw, default="0") -> Decimal:
    try:
        value = Decimal(str(raw))
        return value if value.is_finite() and value >= 0 else Decimal(default)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def cost_unit(card: dict, quantity: int) -> Decimal:
    base = safe_decimal(card.get("yourPrice"))
    bulk = card.get("bulkDiscount") or {}
    if bulk.get("enabled"):
        for tier in bulk.get("tiers") or []:
            low, high = tier.get("minQuantity", 0), tier.get("maxQuantity")
            if isinstance(low, int) and quantity >= low and (high is None or quantity <= high):
                tier_price = safe_decimal(tier.get("unitPrice"))
                if tier_price > 0:
                    base = min(base, tier_price) if base else tier_price
    return base


def unit_som(db, item: CatalogItem, quantity: int) -> tuple[int, Decimal, int]:
    card = json.loads(item.data_json)
    cost = cost_unit(card, quantity)
    rate = int(get_setting(db, "exchange_rate"))
    markup = safe_decimal(item.markup_percent if item.markup_percent is not None else get_setting(db, "markup_percent"))
    if item.custom_price_som is not None:
        sale = item.custom_price_som
    else:
        sale = int((cost * rate * (1 + markup / 100)).to_integral_value(rounding=ROUND_CEILING))
    return sale, cost, rate


def public_item(db, item: CatalogItem, lang="uz", quantity=1) -> dict:
    card = json.loads(item.data_json)
    stock = card.get("stock") or {}
    unit, _, _ = unit_som(db, item, quantity)
    desc = getattr(item, "desc_" + lang, None) or card.get("description") or ""
    return {
        "slug": item.slug, "name": getattr(item, "title_" + lang, None) or item.name,
        "description": desc, "provider": (card.get("provider") or {}).get("name") or item.provider_key.title(),
        "providerKey": item.provider_key, "deliveryType": card.get("deliveryType"),
        "imageUrl": item.image_path or f"/api/art/{item.slug}.svg",
        "durationDays": card.get("durationDays"), "warrantyDays": (card.get("warranty") or {}).get("days"),
        "stock": max(0, int(stock.get("count") or 0)),
        "inStock": bool(stock.get("inStock")) and int(stock.get("count") or 0) > 0,
        "maxQuantity": min(50, int(stock.get("maxQuantity") or stock.get("count") or 0)),
        "unitSom": unit, "currency": "UZS", "sortOrder": item.sort_order,
        "bulk": bool((card.get("bulkDiscount") or {}).get("enabled")),
    }


def _upsert(db, card: dict):
    slug = card.get("slug")
    if not isinstance(slug, str) or not slug or len(slug) > 160 or card.get("deliveryType") not in SUPPORTED:
        return
    item = db.scalar(select(CatalogItem).where(CatalogItem.slug == slug))
    if not item:
        item = CatalogItem(slug=slug)
        db.add(item)
    item.name = str(card.get("name") or slug)[:255]
    item.provider_key = str((card.get("provider") or {}).get("key") or "other")[:100]
    item.data_json = json.dumps(card, ensure_ascii=False)
    item.listed = True


async def sync_catalog() -> int:
    with session() as db:
        partner = PartnerClient(db)
    response = await partner.products()
    cards = response.get("data")
    if not isinstance(cards, list):
        raise ServiceError("PARTNER_ERROR", "Invalid catalog response", 502)
    with session() as db:
        for item in db.scalars(select(CatalogItem)).all():
            item.listed = False
        for card in cards:
            if isinstance(card, dict):
                _upsert(db, card)
    return len(cards)


async def refresh_product(slug: str) -> None:
    from .config import config
    if config.demo:
        return
    with session() as db:
        partner = PartnerClient(db)
    response = await partner.product(slug)
    # Single product endpoints may return {data: {...}} or the product directly.
    card = response.get("data") or response.get("product") or response
    if not isinstance(card, dict) or card.get("slug") != slug:
        raise ServiceError("PRODUCT_NOT_FOUND", "Product no longer exists", 404)
    with session() as db:
        _upsert(db, card)
