"""Telegram WebApp HMAC authentication and same-origin, CSRF-protected sessions."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl, urlparse

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import config
from .db import SessionLocal
from .models import User

SESSION_COOKIE = "activon_session"
MAX_AGE = 7 * 24 * 3600


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def serializer():
    return URLSafeTimedSerializer(config.session_secret or "demo-session-secret-not-for-deployment-00000000", salt="activon-webapp-session-v1")


def verify_init_data(raw: str) -> dict:
    """Telegram's WebAppData algorithm, including freshness and duplicate-key rejection."""
    if not config.bot_token or len(raw) > 8192:
        raise HTTPException(401, "Invalid Telegram authentication")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise HTTPException(401, "Invalid Telegram authentication") from None
    data = dict(pairs)
    if len(data) != len(pairs) or "hash" not in data or "auth_date" not in data or "user" not in data:
        raise HTTPException(401, "Invalid Telegram authentication")
    try:
        age = time.time() - int(data["auth_date"])
    except ValueError:
        raise HTTPException(401, "Invalid Telegram authentication") from None
    if age < -60 or age > 86400:
        raise HTTPException(401, "Expired Telegram authentication")
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()) if key != "hash")
    secret_key = hmac.new(b"WebAppData", config.bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, data["hash"]):
        raise HTTPException(401, "Invalid Telegram authentication")
    try:
        user = json.loads(data["user"])
        if not isinstance(user["id"], int) or user["id"] <= 0:
            raise ValueError()
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Telegram user") from None
    return user


def login_telegram(db: Session, telegram: dict) -> User:
    uid = telegram["id"]
    user = db.scalar(select(User).where(User.telegram_id == uid))
    if not user:
        user = User(telegram_id=uid, first_name="", role="ADMIN" if uid in config.admin_ids else "CUSTOMER")
        db.add(user)
    user.first_name = str(telegram.get("first_name") or "User")[:100]
    user.username = str(telegram.get("username") or "")[:64] or None
    photo = str(telegram.get("photo_url") or "")
    user.photo_url = photo[:1024] if urlparse(photo).scheme == "https" else None
    lang = str(telegram.get("language_code") or "uz")[:2].lower()
    if user.id is None or user.language not in ("uz", "ru", "en"):
        user.language = lang if lang in ("uz", "ru", "en") else "uz"
    if uid in config.admin_ids:
        user.role = "ADMIN"
        user.blocked = False
    db.commit()
    db.refresh(user)
    return user


def set_session(response, request: Request, user: User):
    token = serializer().dumps({"uid": user.id, "nonce": secrets.token_hex(10)})
    # Always secure in production. A localhost HTTP demo needs a non-secure cookie.
    secure = not config.demo or request.headers.get("x-forwarded-proto", "") == "https" or request.url.scheme == "https"
    response.set_cookie(SESSION_COOKIE, token, max_age=MAX_AGE, httponly=True, secure=secure,
                        samesite="none" if secure else "lax", path="/")
    return csrf_for(token)


def csrf_for(token: str) -> str:
    secret = (config.session_secret or "demo-session-secret-not-for-deployment-00000000").encode()
    return hmac.new(secret, ("csrf:" + token).encode(), hashlib.sha256).hexdigest()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    try:
        payload = serializer().loads(cookie, max_age=MAX_AGE)
        user_id = int(payload["uid"])
    except (BadSignature, SignatureExpired, ValueError, TypeError, KeyError):
        raise HTTPException(401, "Sign in with Telegram") from None
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "Account not found")
    if user.blocked:
        raise HTTPException(403, "Account is blocked")
    return user


def csrf_user(request: Request, user: User = Depends(current_user)) -> User:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    submitted = request.headers.get("X-CSRF-Token", "")
    if not submitted or not hmac.compare_digest(submitted, csrf_for(cookie)):
        raise HTTPException(403, "Invalid CSRF token")
    # Cross-site form requests cannot add the custom header; enforce same-origin as well.
    origin = request.headers.get("origin")
    if origin:
        hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")}
        if urlparse(origin).netloc not in hosts:
            raise HTTPException(403, "Invalid request origin")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN" and user.telegram_id not in config.admin_ids:
        raise HTTPException(403, "Administrator access required")
    return user


def admin_write(request: Request, user: User = Depends(csrf_user)) -> User:
    if user.role != "ADMIN" and user.telegram_id not in config.admin_ids:
        raise HTTPException(403, "Administrator access required")
    return user
