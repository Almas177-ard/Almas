"""FastAPI storefront, secure Telegram auth, Hamyon callbacks and operator console."""
from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import re
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import (SESSION_COOKIE, admin_user, admin_write, csrf_for, csrf_user,
                   current_user, get_db, login_telegram, set_session, verify_init_data)
from .catalog import public_item, sync_catalog, unit_som
from .commerce import (OPEN_INVOICES, ServiceError, apply_paid, checkout, fulfill_order, handle_callback, iso, order_view, payment_view,
                       reconcile_payment, refund_order, topup, worker_loop)
from .config import config
from .db import DEFAULTS, SECRET_KEYS, get_setting, init_db, session, set_setting
from .models import Audit, CatalogItem, Order, Payment, TextOverride, User, WalletEntry, now
from .providers import PartnerClient, validate_base_url

ROOT = Path(__file__).resolve().parent.parent


async def catalog_loop():
    while True:
        await asyncio.sleep(120)
        try:
            with session() as db:
                ready = bool(get_setting(db, "partner_api_key"))
            if ready:
                await sync_catalog()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # admin console exposes connectivity errors on demand


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    tasks = [asyncio.create_task(worker_loop())]
    if not config.demo:
        tasks.append(asyncio.create_task(catalog_loop()))
    if config.bot_token and config.webapp_url:
        from .bot import run_bot
        tasks.append(asyncio.create_task(run_bot()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Activon", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=ROOT / "uploads"), name="uploads")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path.startswith("/api/") or request.url.path.startswith("/webhooks/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    error = {"code": exc.code, "message": exc.message}
    if exc.request_id:
        error["requestId"] = exc.request_id
    return JSONResponse({"error": error}, status_code=exc.status)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(exc.status_code, "INVALID_REQUEST")
    return JSONResponse({"error": {"code": code, "message": str(exc.detail)}}, status_code=exc.status_code)


@app.get("/")
def index():
    return FileResponse(ROOT / "static/index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(ROOT / "static/admin.html")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "activon", "demo": config.demo}


@app.get("/api/public/config")
def public_config(db: Session = Depends(get_db)):
    overrides = {lang: {} for lang in ("uz", "ru", "en")}
    for row in db.scalars(select(TextOverride)).all():
        if row.lang in overrides:
            overrides[row.lang][row.key] = row.value
    return {"brand": get_setting(db, "brand_name"), "heroImage": get_setting(db, "hero_image"),
            "logoImage": get_setting(db, "logo_image"), "supportUrl": get_setting(db, "support_url"),
            "checkoutEnabled": config.demo or get_setting(db, "checkout_enabled") == "1",
            "maintenance": get_setting(db, "maintenance") == "1", "demo": config.demo,
            "textOverrides": overrides}


class TelegramLogin(BaseModel):
    initData: str = Field(max_length=8192)


@app.post("/api/auth/telegram")
def telegram_login(payload: TelegramLogin, request: Request, db: Session = Depends(get_db)):
    user = login_telegram(db, verify_init_data(payload.initData))
    if user.blocked:
        raise ServiceError("USER_BLOCKED", "Account is blocked", 403)
    response = JSONResponse({"ok": True})
    set_session(response, request, user)
    return response


@app.post("/api/demo/login")
def demo_login(request: Request, db: Session = Depends(get_db)):
    if not config.demo:
        raise HTTPException(404, "Not found")
    user = db.scalar(select(User).where(User.telegram_id == 900000001))
    response = JSONResponse({"ok": True})
    set_session(response, request, user)
    return response


@app.post("/api/auth/logout")
def logout(_: User = Depends(csrf_user)):
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
def me(request: Request, user: User = Depends(current_user)):
    return {"id": user.id, "name": user.first_name, "username": user.username,
            "photoUrl": user.avatar_path or user.photo_url, "telegramId": user.telegram_id,
            "language": user.language, "role": user.role, "balanceSom": user.balance_som,
            "csrfToken": csrf_for(request.cookies[SESSION_COOKIE])}


class Preferences(BaseModel):
    language: str


@app.post("/api/preferences")
def preferences(data: Preferences, user: User = Depends(csrf_user)):
    if data.language not in ("uz", "ru", "en"):
        raise ServiceError("INVALID_LANGUAGE", "Choose uz, ru or en")
    with session() as db:
        db.get(User, user.id).language = data.language
    return {"ok": True}


@app.get("/api/catalog")
def catalog(lang: str = "uz", db: Session = Depends(get_db)):
    if lang not in ("uz", "ru", "en"):
        lang = "uz"
    items = db.scalars(select(CatalogItem).where(CatalogItem.visible.is_(True), CatalogItem.listed.is_(True))
                       .order_by(CatalogItem.sort_order, CatalogItem.id)).all()
    return {"data": [public_item(db, item, lang) for item in items]}


@app.get("/api/catalog/{slug}")
def catalog_detail(slug: str, lang: str = "uz", quantity: int = 1, db: Session = Depends(get_db)):
    item = db.scalar(select(CatalogItem).where(CatalogItem.slug == slug, CatalogItem.visible.is_(True), CatalogItem.listed.is_(True)))
    if not item:
        raise ServiceError("PRODUCT_NOT_FOUND", "Product is not available", 404)
    if lang not in ("uz", "ru", "en"):
        lang = "uz"
    if not 1 <= quantity <= 50:
        raise ServiceError("INVALID_QUANTITY", "Invalid quantity")
    return public_item(db, item, lang, quantity)


@app.get("/api/art/{slug}.svg")
def product_art(slug: str):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", slug):
        raise HTTPException(404, "Not found")
    hue = int(hashlib.sha256(slug.encode()).hexdigest()[:4], 16) % 90 + 185
    letter = html.escape(slug[0].upper())
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="520" height="360" viewBox="0 0 520 360">
<defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="hsl({hue} 83% 69%)"/><stop offset="1" stop-color="hsl({hue + 40} 82% 40%)"/></linearGradient>
<radialGradient id="bg"><stop stop-color="hsl({hue} 90% 94%)"/><stop offset="1" stop-color="hsl({hue} 75% 80%)"/></radialGradient></defs>
<rect width="520" height="360" rx="32" fill="url(#bg)"/><circle cx="275" cy="176" r="148" fill="none" stroke="#fff" stroke-opacity=".54" stroke-width="2"/>
<circle cx="275" cy="176" r="112" fill="none" stroke="#fff" stroke-opacity=".45" stroke-width="2"/><circle cx="275" cy="176" r="80" fill="url(#g)"/>
<text x="275" y="205" text-anchor="middle" font-family="Arial,sans-serif" font-size="88" font-weight="700" fill="white">{letter}</text>
<circle cx="420" cy="82" r="14" fill="white" opacity=".6"/><circle cx="111" cy="285" r="8" fill="white" opacity=".8"/></svg>'''
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public,max-age=86400"})


class CheckoutBody(BaseModel):
    productSlug: str = Field(min_length=1, max_length=160)
    quantity: int = Field(default=1, ge=1, le=50)
    method: str


@app.post("/api/checkout")
async def create_checkout(payload: CheckoutBody, user: User = Depends(csrf_user)):
    return await checkout(user.id, payload.productSlug, payload.quantity, payload.method)


class TopupBody(BaseModel):
    amountSom: int


@app.post("/api/wallet/topup")
async def create_topup(payload: TopupBody, user: User = Depends(csrf_user)):
    return await topup(user.id, payload.amountSom)


@app.get("/api/wallet")
def wallet(user: User = Depends(current_user), db: Session = Depends(get_db)):
    # Query fresh balance, not stale state from auth dependency's session.
    entries = db.scalars(select(WalletEntry).where(WalletEntry.user_id == user.id)
                         .order_by(WalletEntry.id.desc()).limit(50)).all()
    return {"balanceSom": db.get(User, user.id).balance_som,
            "currency": "UZS", "transactions": [{"id": e.id, "kind": e.kind, "amountSom": e.amount_som,
                "balanceAfter": e.balance_after, "createdAt": iso(e.created_at)} for e in entries]}


@app.get("/api/orders")
def my_orders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    orders = db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(100)).all()
    products = {p.slug: p for p in db.scalars(select(CatalogItem).where(CatalogItem.slug.in_([o.product_slug for o in orders]))).all()} if orders else {}
    result = []
    for order in orders:
        view = order_view(order)
        item = products.get(order.product_slug)
        if item:
            view["name"] = getattr(item, "title_" + user.language, None) or order.product_name
        result.append(view)
    return {"data": result}


@app.get("/api/orders/{code}")
def my_order(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.code == code, Order.user_id == user.id))
    if not order:
        raise ServiceError("ORDER_NOT_FOUND", "Order not found", 404)
    result = order_view(order, detail=True)
    item = db.scalar(select(CatalogItem).where(CatalogItem.slug == order.product_slug))
    if item:
        result["name"] = getattr(item, "title_" + user.language, None) or order.product_name
    if order.method == "CARD" and order.status in ("AWAITING_PAYMENT", "PAYMENT_FAILED"):
        payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
        if payment and payment.status in OPEN_INVOICES:
            result["payment"] = payment_view(payment)
    return result


@app.get("/api/payments/{payment_id}")
def my_payment(payment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Payment, payment_id)
    if not p or p.user_id != user.id:
        raise ServiceError("PAYMENT_NOT_FOUND", "Payment not found", 404)
    return payment_view(p)


@app.post("/api/demo/payments/{payment_id}/confirm")
async def demo_confirm(payment_id: str, user: User = Depends(csrf_user)):
    if not config.demo:
        raise HTTPException(404, "Not found")
    with session() as db:
        p = db.query(Payment).filter_by(id=payment_id).with_for_update().one_or_none()
        if not p or p.user_id != user.id:
            raise HTTPException(404, "Not found")
        if p.status not in ("PENDING", "PAID"):
            raise ServiceError("INVALID_PAYMENT_STATE", "Invoice cannot be confirmed", 409)
    oid = apply_paid(payment_id)
    if oid:
        await fulfill_order(oid)
    return {"ok": True}


async def _webhook_payload(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 8192:
        raise ServiceError("INVALID_CALLBACK", "Callback is too large", 400)
    try:
        if "application/json" in request.headers.get("content-type", ""):
            data = json.loads(raw)
        else:
            pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
            data = dict(pairs)
            if len(pairs) != len(data):
                raise ValueError("Duplicate fields")
        if not isinstance(data, dict):
            raise ValueError("Invalid data")
        return data
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ServiceError("INVALID_CALLBACK", "Malformed callback", 400) from None


@app.post("/webhooks/hamyon/prepare")
async def hamyon_prepare(request: Request):
    await handle_callback(await _webhook_payload(request), prepare=True)
    return {"ok": True}


@app.post("/webhooks/hamyon/complete")
async def hamyon_complete(request: Request):
    oid = await handle_callback(await _webhook_payload(request))
    if oid:
        # Acknowledgement is durable before the slower Partner API request.
        asyncio.create_task(fulfill_order(oid))
    return {"ok": True}


# ---------- Operator API: every route checks a signed Telegram session ----------
@app.get("/api/admin/overview")
def admin_overview(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    all_orders = db.scalars(select(Order).order_by(Order.id.desc()).limit(6)).all()
    totals = db.execute(select(func.coalesce(func.sum(Order.total_som), 0),
        func.coalesce(func.sum(Order.upstream_unit_usd * Order.rate_snapshot * Order.quantity), 0)
        ).where(Order.status == "DELIVERED")).one()
    revenue, expense = int(totals[0]), int(totals[1])
    series = []
    for offset in range(6, -1, -1):
        day = (now() - timedelta(days=offset)).date()
        start = now().replace(year=day.year, month=day.month, day=day.day, hour=0, minute=0, second=0, microsecond=0)
        amount = db.scalar(select(func.coalesce(func.sum(Order.total_som), 0)).where(
            Order.status == "DELIVERED", Order.created_at >= start,
            Order.created_at < start + timedelta(days=1))) or 0
        series.append({"date": day.isoformat(), "amountSom": int(amount)})
    return {"users": db.scalar(select(func.count(User.id))) or 0,
            "orders": db.scalar(select(func.count(Order.id))) or 0,
            "needsAttention": db.scalar(select(func.count(Order.id)).where(Order.status == "NEEDS_ATTENTION")) or 0,
            "revenueSom": revenue, "estimatedProfitSom": revenue - expense,
            "recent": [order_view(o) for o in all_orders], "series": series}


@app.get("/api/admin/products")
def admin_products(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    out = []
    for item in db.scalars(select(CatalogItem).order_by(CatalogItem.sort_order, CatalogItem.id)).all():
        info = public_item(db, item)
        _, cost, rate = unit_som(db, item, 1)
        info.update(visible=item.visible, listed=item.listed, estimatedCostSom=int(cost * rate),
                    customPriceSom=item.custom_price_som, markupPercent=float(item.markup_percent) if item.markup_percent is not None else None,
                    titles={l: getattr(item, "title_" + l) or "" for l in ("uz", "ru", "en")},
                    descriptions={l: getattr(item, "desc_" + l) or "" for l in ("uz", "ru", "en")})
        out.append(info)
    return {"data": out}


class ProductEdit(BaseModel):
    visible: bool
    customPriceSom: int | None = Field(default=None, ge=1, le=1000000000)
    markupPercent: float | None = Field(default=None, ge=0, le=1000)
    sortOrder: int = Field(default=0, ge=-10000, le=10000)
    titles: dict[str, str] = Field(default_factory=dict)
    descriptions: dict[str, str] = Field(default_factory=dict)
    imagePath: str | None = None


@app.put("/api/admin/products/{slug}")
def edit_product(slug: str, data: ProductEdit, actor: User = Depends(admin_write)):
    if data.imagePath and not re.fullmatch(r"/(?:uploads/[a-f0-9]{32}\.webp|static/img/products/[a-z0-9_-]+\.svg)", data.imagePath):
        raise ServiceError("INVALID_IMAGE", "Upload a product image first")
    with session() as db:
        item = db.scalar(select(CatalogItem).where(CatalogItem.slug == slug))
        if not item:
            raise ServiceError("PRODUCT_NOT_FOUND", "Product not found", 404)
        item.visible = data.visible
        item.custom_price_som = data.customPriceSom
        item.markup_percent = data.markupPercent
        item.sort_order = data.sortOrder
        item.image_path = data.imagePath or None
        for lang in ("uz", "ru", "en"):
            setattr(item, "title_" + lang, data.titles.get(lang, "")[:255] or None)
            setattr(item, "desc_" + lang, data.descriptions.get(lang, "")[:2000] or None)
        db.add(Audit(actor_id=actor.id, action="product.edit", target=slug))
    return {"ok": True}


@app.post("/api/admin/catalog/sync")
async def catalog_sync(actor: User = Depends(admin_write)):
    if config.demo:
        return {"count": 6, "demo": True}
    count = await sync_catalog()
    with session() as db:
        db.add(Audit(actor_id=actor.id, action="catalog.sync", target="catalog", detail=str(count)))
    return {"count": count}


@app.get("/api/admin/orders")
def admin_orders(status: str | None = None, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
    query = select(Order).order_by(Order.id.desc()).limit(200)
    if status:
        query = query.where(Order.status == status)
    orders = db.scalars(query).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([o.user_id for o in orders]))).all()} if orders else {}
    return {"data": [{**order_view(o), "customer": users[o.user_id].first_name,
                      "telegramId": users[o.user_id].telegram_id, "attempts": o.attempts} for o in orders]}


@app.post("/api/admin/orders/{code}/retry")
async def retry_order(code: str, actor: User = Depends(admin_write)):
    with session() as db:
        order = db.query(Order).filter_by(code=code).with_for_update().one_or_none()
        if not order:
            raise ServiceError("ORDER_NOT_FOUND", "Order not found", 404)
        if order.status not in ("NEEDS_ATTENTION", "RETRY_WAIT"):
            raise ServiceError("INVALID_ORDER_STATE", "Order is not eligible for retry", 409)
        order.status = "READY"
        order.attempts = 0
        db.add(Audit(actor_id=actor.id, action="order.retry", target=code))
        oid = order.id
    await fulfill_order(oid)
    with session() as db:
        return {"order": order_view(db.get(Order, oid))}


@app.post("/api/admin/orders/{code}/refund")
def admin_refund(code: str, actor: User = Depends(admin_write)):
    with session() as db:
        order = db.scalar(select(Order).where(Order.code == code))
        if not order:
            raise ServiceError("ORDER_NOT_FOUND", "Order not found", 404)
        if order.status == "FULFILLING":
            raise ServiceError("INVALID_ORDER_STATE", "Wait for the active fulfillment attempt", 409)
        oid = order.id
    refund_order(oid, actor_id=actor.id, reason="Operator wallet refund")
    return {"ok": True, "note": "Wallet credited; card transfer not reversed"}


@app.get("/api/admin/payments")
def admin_payments(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    payments = db.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(200)).all()
    users = {u.id: u.first_name for u in db.scalars(select(User).where(User.id.in_([p.user_id for p in payments]))).all()} if payments else {}
    return {"data": [{**payment_view(p), "customer": users.get(p.user_id, ""),
                      "providerId": p.provider_payment_id} for p in payments]}


@app.post("/api/admin/payments/{pid}/reconcile")
async def admin_reconcile(pid: str, actor: User = Depends(admin_write)):
    if config.demo:
        raise ServiceError("DEMO_ONLY", "Demo confirmation is in the shop", 400)
    oid = await reconcile_payment(pid)
    if oid:
        await fulfill_order(oid)
    with session() as db:
        db.add(Audit(actor_id=actor.id, action="payment.reconcile", target=pid))
        p = db.get(Payment, pid)
        if not p:
            raise ServiceError("PAYMENT_NOT_FOUND", "Payment not found", 404)
        return payment_view(p)


@app.get("/api/admin/users")
def admin_users(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id.desc()).limit(300)).all()
    return {"data": [{"id": u.id, "telegramId": u.telegram_id, "name": u.first_name,
                       "username": u.username, "photoUrl": u.avatar_path or u.photo_url,
                       "role": u.role, "blocked": u.blocked,
                       "balanceSom": u.balance_som, "createdAt": iso(u.created_at)} for u in users]}


class UserEdit(BaseModel):
    blocked: bool | None = None
    role: str | None = None
    adjustmentSom: int | None = Field(default=None, ge=-100000000, le=100000000)
    note: str | None = Field(default=None, max_length=200)


@app.patch("/api/admin/users/{user_id}")
def edit_user(user_id: int, data: UserEdit, actor: User = Depends(admin_write)):
    with session() as db:
        target = db.query(User).filter_by(id=user_id).with_for_update().one_or_none()
        if not target:
            raise ServiceError("USER_NOT_FOUND", "User not found", 404)
        if target.id == actor.id and (data.blocked is True or data.role == "CUSTOMER"):
            raise ServiceError("INVALID_ACTION", "Cannot lock yourself out")
        if data.blocked is not None:
            if target.telegram_id in config.admin_ids and data.blocked:
                raise ServiceError("INVALID_ACTION", "Bootstrap administrator cannot be blocked")
            target.blocked = data.blocked
        if data.role is not None:
            if actor.telegram_id not in config.admin_ids or data.role not in ("CUSTOMER", "ADMIN") or target.telegram_id in config.admin_ids:
                raise ServiceError("FORBIDDEN", "Only the bootstrap administrator can change roles", 403)
            target.role = data.role
        if data.adjustmentSom is not None and data.adjustmentSom != 0:
            if target.balance_som + data.adjustmentSom < 0:
                raise ServiceError("INSUFFICIENT_WALLET", "Adjustment would make balance negative")
            target.balance_som += data.adjustmentSom
            db.add(WalletEntry(user_id=target.id, kind="ADJUSTMENT", amount_som=data.adjustmentSom,
                               balance_after=target.balance_som, note=(data.note or "Operator adjustment")[:200]))
        db.add(Audit(actor_id=actor.id, action="user.edit", target=str(user_id),
                     detail=f"blocked={data.blocked};role={data.role};adjustmentSom={data.adjustmentSom}"))
    return {"ok": True}


@app.get("/api/admin/settings")
def admin_settings(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return {**{key: get_setting(db, key) for key in DEFAULTS if key != "invoice_lock"},
            "partner_api_key_configured": bool(get_setting(db, "partner_api_key")),
            "hamyon_shop_key_configured": bool(get_setting(db, "hamyon_shop_key")),
            "callbackPrepare": "/webhooks/hamyon/prepare", "callbackComplete": "/webhooks/hamyon/complete"}


@app.put("/api/admin/settings")
def update_settings(values: dict = Body(...), actor: User = Depends(admin_write)):
    allowed = set(DEFAULTS) | SECRET_KEYS
    if not values or any(k not in allowed or k == "invoice_lock" for k in values):
        raise ServiceError("VALIDATION_ERROR", "Unknown setting")
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 1000:
            raise ServiceError("VALIDATION_ERROR", "Invalid setting value")
        if key in ("partner_base_url", "hamyon_base_url"):
            try:
                validate_base_url(value)
            except ValueError:
                raise ServiceError("VALIDATION_ERROR", "Use a public HTTPS URL") from None
        if key == "exchange_rate" and (not value.isdigit() or not 100 <= int(value) <= 1000000):
            raise ServiceError("VALIDATION_ERROR", "Exchange rate must be between 100 and 1,000,000")
        if key == "markup_percent":
            try:
                if not 0 <= Decimal(value) <= 1000:
                    raise ValueError()
            except Exception:
                raise ServiceError("VALIDATION_ERROR", "Markup must be between 0 and 1000%") from None
        if key in ("checkout_enabled", "maintenance") and value not in ("0", "1"):
            raise ServiceError("VALIDATION_ERROR", "Expected 0 or 1")
        if key in ("hero_image", "logo_image") and not re.fullmatch(r"/(?:uploads/[a-f0-9]{32}\.webp|static/img/[a-zA-Z0-9_./-]+\.(?:svg|png|webp|jpg))", value):
            raise ServiceError("VALIDATION_ERROR", "Upload an image first")
        if key == "hero_image" and value.endswith(".svg"):
            raise ServiceError("VALIDATION_ERROR", "Use PNG, JPEG or WebP for the bot cover image")
        if key == "support_url" and not value.startswith("https://"):
            raise ServiceError("VALIDATION_ERROR", "Use an HTTPS support link")
        if key == "brand_name" and not 2 <= len(value.strip()) <= 40:
            raise ServiceError("VALIDATION_ERROR", "Brand name must be 2–40 characters")
    with session() as db:
        if values.get("checkout_enabled") == "1" and not config.demo:
            def effective(name):
                return values[name].strip() if name in values else get_setting(db, name)
            if not all(effective(name) for name in ("hamyon_shop_id", "hamyon_shop_key", "partner_api_key")):
                raise ServiceError("VALIDATION_ERROR", "Configure both providers before enabling checkout")
        for key, value in values.items():
            set_setting(db, key, value.strip())
        db.add(Audit(actor_id=actor.id, action="settings.update", target="settings",
                     detail=",".join(sorted(values.keys()))[:400]))  # never log secret values
    return {"ok": True}


@app.get("/api/admin/partner")
async def partner_info(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    if config.demo:
        return {"connected": True, "demo": True, "balanceSom": 18500000,
                "apiOrdersTotal": 42, "apiOrders24h": 5, "requestsToday": 128, "errorsToday": 2}
    client = PartnerClient(db)
    health = await client.health()
    balance = await client.balance()
    usage = await client.usage()
    rate = int(get_setting(db, "exchange_rate"))
    return {"connected": bool(health.get("ok")), "balanceSom": int(Decimal(str(balance["balance"])) * rate),
            "apiOrdersTotal": int(usage.get("apiOrdersTotal") or 0),
            "apiOrders24h": int(usage.get("apiOrders24h") or 0),
            "requestsToday": int(usage.get("requestCountToday") or 0),
            "errorsToday": int(usage.get("errorCountToday") or 0),
            "spendSom": int(Decimal(str(usage.get("apiSpendTotal") or 0)) * rate)}


@app.get("/api/admin/texts")
def admin_texts(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return {"data": [{"key": t.key, "lang": t.lang, "value": t.value} for t in db.scalars(select(TextOverride)).all()]}


class TextEdit(BaseModel):
    key: str = Field(min_length=2, max_length=120)
    lang: str
    value: str = Field(max_length=1500)


@app.put("/api/admin/texts")
def edit_texts(entries: list[TextEdit], actor: User = Depends(admin_write)):
    if len(entries) > 200:
        raise ServiceError("VALIDATION_ERROR", "Too many texts")
    with session() as db:
        for entry in entries:
            if entry.lang not in ("uz", "ru", "en") or not re.fullmatch(r"[a-zA-Z0-9_.-]+", entry.key):
                raise ServiceError("VALIDATION_ERROR", "Invalid language or text key")
            if entry.key == "bot.start" and len(entry.value) > 950:
                raise ServiceError("VALIDATION_ERROR", "Bot caption must be under 950 characters")
            if entry.key == "bot.open" and len(entry.value) > 64:
                raise ServiceError("VALIDATION_ERROR", "Bot button text must be under 64 characters")
            row = db.get(TextOverride, (entry.key, entry.lang))
            if not entry.value.strip():
                if row:
                    db.delete(row)  # empty = reset translation to built-in default
            elif row:
                row.value = entry.value.strip()
            else:
                db.add(TextOverride(key=entry.key, lang=entry.lang, value=entry.value.strip()))
        db.add(Audit(actor_id=actor.id, action="texts.update", target="translations", detail=f"{len(entries)} entries"))
    return {"ok": True}


async def read_image_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 3 * 1024 * 1024:
            raise ServiceError("INVALID_IMAGE", "Image must be smaller than 3 MB")
    return bytes(body)


def save_image(raw: bytes, content_type: str, *, avatar=False) -> str:
    if content_type.split(";")[0] not in ("image/png", "image/jpeg", "image/webp"):
        raise ServiceError("INVALID_IMAGE", "Use PNG, JPEG or WebP")
    if len(raw) > 3 * 1024 * 1024 or not raw:
        raise ServiceError("INVALID_IMAGE", "Image must be smaller than 3 MB")
    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        image = Image.open(io.BytesIO(raw))
        if image.width > 4096 or image.height > 4096 or image.width < 50 or image.height < 50:
            raise ValueError("Invalid dimensions")
        image.thumbnail((640, 640) if avatar else (1600, 1200))
        image = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
        filename = secrets.token_hex(16) + ".webp"
        image.save(ROOT / "uploads" / filename, "WEBP", quality=86, method=5)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ServiceError("INVALID_IMAGE", "Invalid or unsafe image") from None
    return "/uploads/" + filename


@app.post("/api/profile/avatar")
async def profile_avatar(request: Request, user: User = Depends(csrf_user)):
    with session() as db:
        recent = db.scalar(select(func.count(Audit.id)).where(Audit.actor_id == user.id,
            Audit.action == "profile.avatar", Audit.created_at > now() - timedelta(minutes=1))) or 0
        if recent >= 3:
            raise ServiceError("RATE_LIMIT_EXCEEDED", "Wait before changing your photo again", 429)
    url = save_image(await read_image_body(request), request.headers.get("content-type", ""), avatar=True)
    with session() as db:
        db.get(User, user.id).avatar_path = url
        db.add(Audit(actor_id=user.id, action="profile.avatar", target=str(user.id)))
    return {"photoUrl": url}


@app.post("/api/admin/media")
async def upload_image(request: Request, actor: User = Depends(admin_write)):
    url = save_image(await read_image_body(request), request.headers.get("content-type", ""))
    with session() as db:
        db.add(Audit(actor_id=actor.id, action="media.upload", target=url.split("/")[-1]))
    return {"url": url}


@app.get("/api/admin/audit")
def admin_audit(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100)).all()
    return {"data": [{"action": r.action, "target": r.target, "detail": r.detail,
                       "createdAt": iso(r.created_at)} for r in rows]}
