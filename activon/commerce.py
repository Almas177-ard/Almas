"""Payment, wallet and upstream fulfillment state machine.

A verified paid invoice creates one fulfillment job. Partner retries reuse the SAME
externalOrderId; DB row claims and upstream idempotency protect against duplicates.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from .catalog import SUPPORTED, refresh_product, unit_som
from .config import config
from .db import decrypt, encrypt, get_setting, session
from .models import Audit, CatalogItem, Order, Payment, Setting, User, WalletEntry, now
from .providers import HamyonClient, PartnerClient, ServiceError

OPEN_INVOICES = ("CREATING", "PENDING", "SETUP_UNKNOWN")
FINAL_ORDER = ("DELIVERED", "REFUNDED", "CANCELLED")
PERMANENT_PARTNER_ERRORS = {"OUT_OF_STOCK", "PRODUCT_NOT_FOUND", "PRODUCT_NOT_ALLOWED", "PRODUCT_UNAVAILABLE", "UNSUPPORTED_DELIVERY_TYPE"}


def iso(date):
    return date.isoformat(timespec="seconds") + "Z" if date else None


def order_view(order: Order, *, detail=False) -> dict:
    obj = dict(code=order.code, productSlug=order.product_slug, name=order.product_name,
               imageUrl=order.image_path or f"/api/art/{order.product_slug}.svg",
               deliveryType=order.delivery_type, quantity=order.quantity,
               unitSom=order.unit_som, totalSom=order.total_som, currency="UZS",
               method=order.method, status=order.status, createdAt=iso(order.created_at),
               upstreamOrderCode=order.upstream_order_code,
               errorCode=order.last_error_code if order.status in ("NEEDS_ATTENTION", "REFUNDED") else None,
               errorRequestId=order.last_error_request_id if order.status in ("NEEDS_ATTENTION", "REFUNDED") else None)
    if detail and order.status == "DELIVERED" and order.delivery_enc:
        obj["delivery"] = json.loads(decrypt(order.delivery_enc))
    return obj


def payment_view(p: Payment) -> dict:
    return dict(id=p.id, orderId=p.order_id, purpose=p.purpose, amountSom=p.amount_som,
                status=p.status, card=p.card if p.status in OPEN_INVOICES else None,
                expiresAt=iso(p.expires_at), createdAt=iso(p.created_at), currency="UZS")


def _allocate_amount(db, base: int) -> int:
    # Serialize allocation across *all* users with one locked MySQL row.
    db.query(Setting).filter(Setting.key == "invoice_lock").with_for_update().one()
    used = set(db.scalars(select(Payment.amount_som).where(
        Payment.status.in_(OPEN_INVOICES),
        (Payment.expires_at.is_(None)) | (Payment.expires_at > now() - timedelta(seconds=30)),
        Payment.amount_som >= base, Payment.amount_som <= base + 100,
    )).all())
    for extra in range(0, 101):
        if base + extra not in used:
            return base + extra
    raise ServiceError("PAYMENT_BUSY", "Too many invoices for this amount", 429)


def _create_invoice(db, user: User, *, purpose: str, base_amount: int, order: Order | None) -> Payment:
    amount = _allocate_amount(db, base_amount)
    payment = Payment(id="PAY-" + secrets.token_hex(12).upper(), user_id=user.id,
                      order_id=order.id if order else None, purpose=purpose, amount_som=amount,
                      status="CREATING", expires_at=now() + timedelta(minutes=7))
    if order:
        order.total_som = amount  # Any +1..100 UZS collision offset is shown before payment.
    if not config.demo:
        payment.shop_id_snapshot = get_setting(db, "hamyon_shop_id")
        payment.signing_key_enc = encrypt(get_setting(db, "hamyon_shop_key"))
    db.add(payment)
    db.flush()
    return payment


async def finish_invoice(payment_id: str) -> dict:
    if config.demo:
        with session() as db:
            p = db.get(Payment, payment_id)
            p.provider_payment_id = "demo-" + payment_id
            p.card = "TEST PAYMENT ONLY"
            p.status = "PENDING"
            p.expires_at = now() + timedelta(minutes=5)
            return payment_view(p)
    with session() as db:
        p = db.get(Payment, payment_id)
        client = HamyonClient(db)
        amount = p.amount_som
    try:
        result = await client.create(payment_id, amount)
        with session() as db:
            p = db.query(Payment).filter_by(id=payment_id).with_for_update().one()
            p.provider_payment_id = str(result["payment_id"])[:128]
            p.card = str(result.get("card") or "")[:36]
            p.expires_at = now() + timedelta(seconds=min(max(int(result.get("expires_in") or 300), 60), 600))
            if p.status == "CREATING":
                p.status = "PENDING"
            return payment_view(p)
    except ServiceError as exc:
        with session() as db:
            p = db.get(Payment, payment_id)
            if p.status == "CREATING":
                p.status = "SETUP_UNKNOWN" if exc.code == "PAYMENT_SETUP_UNKNOWN" else "SETUP_FAILED"
            if p.order_id:
                order = db.get(Order, p.order_id)
                if order.status == "AWAITING_PAYMENT":
                    order.status = "PAYMENT_FAILED"
                    order.last_error_code = exc.code
        raise


async def checkout(user_id: int, slug: str, quantity: int, method: str) -> dict:
    if method not in ("CARD", "WALLET") or not 1 <= quantity <= 50:
        raise ServiceError("INVALID_QUANTITY", "Choose a valid payment method and quantity")
    with session() as db:
        if get_setting(db, "maintenance") == "1":
            raise ServiceError("MAINTENANCE", "Store is temporarily closed", 503)
        if get_setting(db, "checkout_enabled") != "1" and not config.demo:
            raise ServiceError("CHECKOUT_DISABLED", "Checkout has not been enabled", 503)
        if method == "CARD" and not config.demo:
            HamyonClient(db)  # fail BEFORE creating an order if credentials are missing
    await refresh_product(slug)  # latest upstream stock/price before charging the customer
    with session() as db:
        user = db.query(User).filter_by(id=user_id).with_for_update().one()
        if user.blocked:
            raise ServiceError("USER_BLOCKED", "Account is blocked", 403)
        recent = db.scalar(select(func.count(Order.id)).where(Order.user_id == user.id,
            Order.created_at >= now() - timedelta(minutes=1))) or 0
        if recent >= 10:
            raise ServiceError("RATE_LIMIT_EXCEEDED", "Too many orders", 429)
        if method == "CARD":
            invoices = db.scalar(select(func.count(Payment.id)).where(Payment.user_id == user.id,
                Payment.created_at >= now() - timedelta(minutes=1))) or 0
            if invoices >= 5:
                raise ServiceError("RATE_LIMIT_EXCEEDED", "Too many invoices", 429)
        item = db.scalar(select(CatalogItem).where(CatalogItem.slug == slug))
        if not item or not item.listed or not item.visible:
            raise ServiceError("PRODUCT_NOT_FOUND", "Product is not available", 404)
        card = json.loads(item.data_json)
        stock = card.get("stock") or {}
        if card.get("deliveryType") not in SUPPORTED:
            raise ServiceError("PRODUCT_UNAVAILABLE", "Product is not an instant-delivery product")
        if not stock.get("inStock") or int(stock.get("count") or 0) < quantity or quantity > min(50, int(stock.get("maxQuantity") or stock.get("count") or 0)):
            raise ServiceError("OUT_OF_STOCK", "Not enough stock available")
        unit, upstream_cost, rate = unit_som(db, item, quantity)
        if upstream_cost <= 0 or unit <= 0:
            raise ServiceError("PRODUCT_UNAVAILABLE", "Product pricing is unavailable")
        price = unit * quantity
        order = Order(code="ACT-" + now().strftime("%y%m%d") + "-" + secrets.token_hex(4).upper(),
                      user_id=user.id, product_slug=slug, product_name=getattr(item, "title_" + user.language, None) or item.name,
                      image_path=item.image_path, delivery_type=card["deliveryType"],
                      quantity=quantity, unit_som=unit, total_som=price,
                      upstream_unit_usd=upstream_cost, rate_snapshot=rate, method=method,
                      status="AWAITING_PAYMENT" if method == "CARD" else "READY",
                      external_order_id="activon-" + secrets.token_hex(16))
        db.add(order)
        db.flush()
        if method == "WALLET":
            if user.balance_som < price:
                raise ServiceError("INSUFFICIENT_WALLET", "Wallet balance is too low")
            user.balance_som -= price
            db.add(WalletEntry(user_id=user.id, order_id=order.id, kind="PURCHASE",
                               amount_som=-price, balance_after=user.balance_som))
        else:
            payment = _create_invoice(db, user, purpose="PRODUCT", base_amount=price, order=order)
            payment_id = payment.id
        order_id = order.id
    if method == "WALLET":
        await fulfill_order(order_id)
        with session() as db:
            return {"order": order_view(db.get(Order, order_id), detail=True)}
    try:
        invoice = await finish_invoice(payment_id)
    except ServiceError as exc:
        with session() as db:
            order = db.get(Order, order_id)
            return {"order": order_view(order), "error": exc.code}
    with session() as db:
        return {"order": order_view(db.get(Order, order_id)), "payment": invoice}


async def topup(user_id: int, amount_som: int) -> dict:
    if not isinstance(amount_som, int) or isinstance(amount_som, bool) or not 10000 <= amount_som <= 10000000:
        raise ServiceError("INVALID_AMOUNT", "Top up between 10,000 and 10,000,000 UZS")
    with session() as db:
        if get_setting(db, "maintenance") == "1":
            raise ServiceError("MAINTENANCE", "Store is temporarily closed", 503)
        if get_setting(db, "checkout_enabled") != "1" and not config.demo:
            raise ServiceError("CHECKOUT_DISABLED", "Checkout has not been enabled", 503)
        if not config.demo:
            HamyonClient(db)
        user = db.query(User).filter_by(id=user_id).with_for_update().one()
        if user.blocked:
            raise ServiceError("USER_BLOCKED", "Account is blocked", 403)
        recent = db.scalar(select(func.count(Payment.id)).where(Payment.user_id == user.id,
            Payment.created_at >= now() - timedelta(minutes=1))) or 0
        if recent >= 5:
            raise ServiceError("RATE_LIMIT_EXCEEDED", "Too many invoices", 429)
        p = _create_invoice(db, user, purpose="TOPUP", base_amount=amount_som, order=None)
        pid = p.id
    try:
        return {"payment": await finish_invoice(pid)}
    except ServiceError as exc:
        return {"error": exc.code}


def refund_order(order_id: int, *, actor_id: int | None = None, reason="REFUND") -> None:
    with session() as db:
        order = db.query(Order).filter_by(id=order_id).with_for_update().one()
        if order.status == "REFUNDED":
            return
        if order.status == "DELIVERED":
            raise ServiceError("ALREADY_DELIVERED", "Delivered orders cannot be refunded automatically")
        if actor_id is not None and order.status == "FULFILLING":
            raise ServiceError("INVALID_ORDER_STATE", "Wait for active fulfillment", 409)
        if order.method == "CARD":
            p = db.scalar(select(Payment).where(Payment.order_id == order.id))
            if not p or p.status != "PAID":
                raise ServiceError("NOT_PAID", "Cannot refund an unpaid order")
        else:
            entry = db.scalar(select(WalletEntry).where(WalletEntry.order_id == order.id, WalletEntry.kind == "PURCHASE"))
            if not entry:
                raise ServiceError("NOT_PAID", "No wallet debit for this order")
        user = db.query(User).filter_by(id=order.user_id).with_for_update().one()
        user.balance_som += order.total_som
        db.add(WalletEntry(user_id=user.id, order_id=order.id, kind="REFUND", amount_som=order.total_som,
                           balance_after=user.balance_som, note=reason[:255]))
        order.status = "REFUNDED"
        order.retry_at = None
        db.add(Audit(actor_id=actor_id, action="order.refund", target=order.code, detail="UZS wallet credit; card not reversed"))


def _extract_delivery(result: dict, order: Order) -> dict:
    """Whitelist sensitive delivery fields; encrypt at rest, never send to logs."""
    fields = {"link", "code", "content", "instructions", "format", "orderCode"}
    if not result.get("ok") or not result.get("orderCode"):
        raise ServiceError("PARTNER_INVALID_RESPONSE", "Partner order response incomplete", 502, True)
    if order.quantity == 1:
        delivery = result.get("delivery")
        if not isinstance(delivery, dict) or not delivery.get({"LINK": "link", "COUPON": "code", "READY_ACCOUNT": "content"}[order.delivery_type]):
            raise ServiceError("PARTNER_INVALID_RESPONSE", "Delivery content missing", 502, True)
        return {"item": {k: v for k, v in delivery.items() if k in fields and isinstance(v, str)}}
    lines = result.get("lines")
    required = {"LINK": "link", "COUPON": "code", "READY_ACCOUNT": "content"}[order.delivery_type]
    if not isinstance(lines, list) or len(lines) != order.quantity or not all(isinstance(line, dict) and line.get(required) for line in lines):
        raise ServiceError("PARTNER_INVALID_RESPONSE", "Bulk delivery incomplete", 502, True)
    return {"lines": [{k: v for k, v in line.items() if k in fields and isinstance(v, str)} for line in lines]}


async def fulfill_order(order_id: int) -> None:
    with session() as db:
        order = db.query(Order).filter_by(id=order_id).with_for_update().one_or_none()
        if not order or order.status not in ("READY", "RETRY_WAIT", "NEEDS_ATTENTION", "FULFILLING"):
            return
        if order.status == "FULFILLING" and order.processing_at and order.processing_at > now() - timedelta(seconds=90):
            return
        if order.status == "RETRY_WAIT" and order.retry_at and order.retry_at > now():
            return
        order.status = "FULFILLING"
        order.processing_at = now()
        order.attempts += 1
        slug, qty, external_id = order.product_slug, order.quantity, order.external_order_id
    try:
        if not config.demo:
            with session() as db:
                partner = PartnerClient(db)
        if config.demo:
            await asyncio.sleep(0.15)
            fields = {"LINK": "link", "COUPON": "code", "READY_ACCOUNT": "content"}
            key = fields[order.delivery_type]
            def fake_line(n):
                return {key: {"LINK": f"https://example.invalid/activon-demo/{order.code}/{n}",
                             "COUPON": f"ACTIVON-DEMO-{order.code[-8:]}-{n}",
                             "READY_ACCOUNT": f"demo-{n}@example.invalid\nDemoOnly!{n}"}[order.delivery_type],
                        "orderCode": f"DEMO-{order.code}-{n}"}
            result = {"ok": True, "orderCode": "DEMO-" + order.code,
                      "delivery": fake_line(1) if qty == 1 else None,
                      "lines": [fake_line(i) for i in range(1, qty + 1)] if qty > 1 else None}
        else:
            await partner.health()
            result = await partner.create_order(slug, qty, external_id)
        # No delivery is logged. Validate then encrypt BEFORE committing success.
        with session() as db:
            order = db.query(Order).filter_by(id=order_id).with_for_update().one()
            if order.status != "FULFILLING":
                return
            delivery = _extract_delivery(result, order)
            order.delivery_enc = encrypt(json.dumps(delivery, ensure_ascii=False))
            try:
                actual_unit = Decimal(str(result.get("unitPrice", "")))
                if actual_unit.is_finite() and 0 < actual_unit <= 100000:
                    order.upstream_unit_usd = actual_unit
            except InvalidOperation:
                pass
            order.upstream_order_code = str(result["orderCode"])[:64]
            order.status = "DELIVERED"
            order.retry_at = None
            order.last_error_code = None
            order.last_error_request_id = None
            db.add(Audit(action="order.delivered", target=order.code, detail="Encrypted delivery stored"))
    except ServiceError as exc:
        if exc.code in PERMANENT_PARTNER_ERRORS:
            with session() as db:
                order = db.query(Order).filter_by(id=order_id).with_for_update().one()
                order.last_error_code = exc.code
                order.last_error_request_id = exc.request_id
                db.add(Audit(action="order.fulfillment_error", target=order.code,
                             detail=exc.code + (" · " + exc.request_id if exc.request_id else "")))
            # Card refund API is not documented; credit the paid sum into Activon wallet.
            refund_order(order_id, reason=exc.code)
            return
        with session() as db:
            order = db.query(Order).filter_by(id=order_id).with_for_update().one()
            if order.status != "FULFILLING":
                return
            order.last_error_code = exc.code
            order.last_error_request_id = exc.request_id
            if exc.retryable and order.attempts < 4:
                order.status = "RETRY_WAIT"
                order.retry_at = now() + timedelta(seconds=min(30 * 2 ** (order.attempts - 1), 240))
            else:
                order.status = "NEEDS_ATTENTION"
            db.add(Audit(action="order.fulfillment_error", target=order.code,
                         detail=exc.code + (" · " + exc.request_id if exc.request_id else "")))
    except Exception:
        # Unexpected failures are recoverable. Do not print delivery or credentials.
        with session() as db:
            order = db.query(Order).filter_by(id=order_id).with_for_update().one()
            if order.status == "FULFILLING":
                order.status = "RETRY_WAIT" if order.attempts < 4 else "NEEDS_ATTENTION"
                order.retry_at = now() + timedelta(seconds=60) if order.status == "RETRY_WAIT" else None
                order.last_error_code = "PARTNER_UNAVAILABLE"


def _parse_amount(raw) -> int:
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value != value.to_integral_value() or value <= 0:
            raise ValueError()
        return int(value)
    except (InvalidOperation, ValueError, TypeError):
        raise ServiceError("INVALID_CALLBACK", "Invalid payment amount", 400) from None


def verify_callback(payload: dict) -> tuple[str, int]:
    payment_id = str(payload.get("payment_id") or "")
    shop_id = str(payload.get("shop_id") or "")
    order_id = str(payload.get("order_id") or "")
    supplied = str(payload.get("sign") or "")
    if not payment_id or not order_id or not shop_id or not re.fullmatch(r"[a-fA-F0-9]{32}", supplied):
        raise ServiceError("INVALID_CALLBACK", "Incomplete signed payment callback", 400)
    amount = _parse_amount(payload.get("amount"))
    with session() as db:
        p = db.query(Payment).filter_by(id=order_id).with_for_update().one_or_none()
        if not p or (p.provider_payment_id and p.provider_payment_id != payment_id) or p.shop_id_snapshot != shop_id or p.amount_som != amount:
            raise ServiceError("INVALID_CALLBACK", "Payment does not match the invoice", 400)
        key = decrypt(p.signing_key_enc)
        expected = hashlib.md5((shop_id + payment_id + str(amount) + key).encode()).hexdigest()
        if not hmac.compare_digest(expected, supplied.lower()):
            raise ServiceError("INVALID_CALLBACK", "Invalid payment signature", 403)
        # A verified signed callback can recover an invoice whose create request timed out.
        # A later server-side GET status still MUST confirm paid before any delivery.
        if not p.provider_payment_id and p.status in ("CREATING", "SETUP_UNKNOWN", "SETUP_FAILED"):
            p.provider_payment_id = payment_id
            p.status = "PENDING"
        elif not p.provider_payment_id:
            raise ServiceError("INVALID_CALLBACK", "Invoice has no provider reference", 400)
    return order_id, amount


def _verify_provider_status(data: dict, p: Payment, desired: str) -> None:
    actual = str(data.get("status") or (data.get("payment") or {}).get("status") or "").lower()
    if actual != desired or str(data.get("payment_id", p.provider_payment_id)) != p.provider_payment_id:
        raise ServiceError("PAYMENT_STATUS_UNAVAILABLE", "Payment status is not confirmed", 503, True)
    if data.get("amount") is not None and _parse_amount(data["amount"]) != p.amount_som:
        raise ServiceError("PAYMENT_MISMATCH", "Payment amount does not match", 403)
    if data.get("order_id") is not None and str(data["order_id"]) != p.id:
        raise ServiceError("PAYMENT_MISMATCH", "Payment order does not match", 403)


def apply_paid(payment_id: str) -> int | None:
    """Atomic, idempotent money transition. Call ONLY after provider status confirmation."""
    with session() as db:
        p = db.query(Payment).filter_by(id=payment_id).with_for_update().one()
        if p.status == "PAID":
            return p.order_id
        if p.status not in (*OPEN_INVOICES, "CANCELLED", "SETUP_FAILED"):
            raise ServiceError("INVALID_PAYMENT_STATE", "Cannot confirm this payment", 409)
        p.status = "PAID"
        if p.purpose == "TOPUP":
            user = db.query(User).filter_by(id=p.user_id).with_for_update().one()
            # unique payment_id on wallet_entries is the last line of defense.
            if not db.scalar(select(WalletEntry).where(WalletEntry.payment_id == p.id)):
                user.balance_som += p.amount_som
                db.add(WalletEntry(user_id=user.id, payment_id=p.id, kind="TOPUP",
                                   amount_som=p.amount_som, balance_after=user.balance_som))
        elif p.order_id:
            order = db.query(Order).filter_by(id=p.order_id).with_for_update().one()
            if order.status in ("AWAITING_PAYMENT", "CANCELLED", "PAYMENT_FAILED"):
                order.status = "READY"
        db.add(Audit(action="payment.paid", target=p.id, detail=f"{p.amount_som} UZS"))
        return p.order_id


def apply_cancel(payment_id: str):
    with session() as db:
        p = db.query(Payment).filter_by(id=payment_id).with_for_update().one()
        if p.status == "PAID":
            return
        if p.status == "CANCELLED":
            return
        p.status = "CANCELLED"
        if p.order_id:
            order = db.query(Order).filter_by(id=p.order_id).with_for_update().one()
            if order.status in ("AWAITING_PAYMENT", "PAYMENT_FAILED"):
                order.status = "CANCELLED"
        db.add(Audit(action="payment.cancelled", target=p.id))


async def handle_callback(payload: dict, *, prepare=False) -> int | None:
    pid, amount = verify_callback(payload)
    if prepare:
        return None  # no money movement on prepare
    state = str(payload.get("status") or "").lower()
    if state not in ("paid", "cancel"):
        raise ServiceError("INVALID_CALLBACK", "Unknown payment status", 400)
    with session() as db:
        payment = db.get(Payment, pid)
        client = HamyonClient(db)
        provider_pid = payment.provider_payment_id
    if payment.status == "PAID":
        return payment.order_id  # duplicate callback: no double credit/purchase
    remote = await client.status(provider_pid)
    _verify_provider_status(remote, payment, state)
    return apply_paid(pid) if state == "paid" else (apply_cancel(pid) or None)


async def reconcile_payment(pid: str) -> int | None:
    """Optional recovery when signed callback was lost (status fetched from provider)."""
    with session() as db:
        p = db.get(Payment, pid)
        if not p or p.status not in OPEN_INVOICES or not p.provider_payment_id:
            return None
        client = HamyonClient(db)
        provider_id = p.provider_payment_id
    remote = await client.status(provider_id)
    state = str(remote.get("status") or (remote.get("payment") or {}).get("status") or "").lower()
    if state in ("paid", "cancel"):
        _verify_provider_status(remote, p, state)
        return apply_paid(pid) if state == "paid" else (apply_cancel(pid) or None)
    return None


async def worker_loop():
    while True:
        try:
            with session() as db:
                candidates = db.scalars(select(Order.id).where(
                    (Order.status == "READY") |
                    ((Order.status == "RETRY_WAIT") & (Order.retry_at <= now())) |
                    ((Order.status == "FULFILLING") & (Order.processing_at <= now() - timedelta(seconds=90)))
                ).order_by(Order.id).limit(10)).all()
            for oid in candidates:
                await fulfill_order(oid)
            if not config.demo:
                with session() as db:
                    pending = db.scalars(select(Payment.id).where(Payment.status == "PENDING",
                        Payment.provider_payment_id.is_not(None), Payment.created_at > now() - timedelta(days=1)
                    ).order_by(Payment.created_at.desc()).limit(15)).all()
                for pid in pending:
                    try:
                        oid = await reconcile_payment(pid)
                        if oid:
                            await fulfill_order(oid)
                    except ServiceError:
                        pass  # callback retry / next reconciliation will recover
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not leak partner delivery material to server logs.
            pass
        await asyncio.sleep(20)
