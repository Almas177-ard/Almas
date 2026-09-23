"""Server-to-server adapters; upstream keys never cross the browser boundary."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from .db import get_setting


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False,
                 request_id: str | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
        self.request_id = request_id


def validate_base_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("An HTTPS base URL without credentials or query is required")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal")) or len(url) > 240:
        raise ValueError("A public HTTPS host is required")
    try:
        ipaddress.ip_address(host)
        raise ValueError("Use a public DNS name, not an IP address")
    except ValueError as exc:
        if str(exc).startswith("Use a public"):
            raise
    return url.rstrip("/")


class PartnerClient:
    def __init__(self, db: Session):
        self.base = validate_base_url(get_setting(db, "partner_base_url"))
        self.key = get_setting(db, "partner_api_key")

    async def request(self, method: str, path: str, *, payload: dict | None = None, auth=True) -> dict:
        if auth and not self.key:
            raise ServiceError("PARTNER_NOT_CONFIGURED", "Partner API key has not been configured", 503)
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.request(method, self.base + path, json=payload,
                    headers={"Authorization": f"Bearer {self.key}"} if auth else {})
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Unexpected response")
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError("PARTNER_UNAVAILABLE", "Partner API could not be reached", 502, True) from exc
        if response.status_code >= 400 or data.get("ok") is False:
            error = data.get("error") or {}
            if not isinstance(error, dict):
                error = {"code": "MAINTENANCE" if response.status_code == 503 else "PARTNER_ERROR"}
            raw_code = str(error.get("code") or "PARTNER_ERROR")
            code = raw_code if re.fullmatch(r"[A-Z0-9_]{2,80}", raw_code) else "PARTNER_ERROR"
            # Never echo untrusted upstream text with potential credentials to a customer.
            trace = str(error.get("requestId") or "")
            trace = trace if re.fullmatch(r"req_[A-Za-z0-9_-]{1,80}", trace) else None
            raise ServiceError(code, "Partner API rejected the request", 502,
                               code in {"RATE_LIMIT_EXCEEDED", "MAINTENANCE", "FAILED", "PARTNER_ERROR"} or response.status_code >= 500,
                               request_id=trace)
        return data

    async def health(self):
        return await self.request("GET", "/health", auth=False)

    async def products(self):
        return await self.request("GET", "/catalog/products")

    async def product(self, slug: str):
        from urllib.parse import quote
        return await self.request("GET", "/catalog/products/" + quote(slug, safe=""))

    async def balance(self):
        return await self.request("GET", "/balance")

    async def usage(self):
        return await self.request("GET", "/usage")

    async def create_order(self, slug: str, quantity: int, external_id: str):
        return await self.request("POST", "/orders", payload={"productSlug": slug, "quantity": quantity, "externalOrderId": external_id})


class HamyonClient:
    def __init__(self, db: Session):
        self.base = validate_base_url(get_setting(db, "hamyon_base_url"))
        self.shop_id = get_setting(db, "hamyon_shop_id")
        self.shop_key = get_setting(db, "hamyon_shop_key")
        if not self.shop_id or not self.shop_key:
            raise ServiceError("PAYMENT_NOT_CONFIGURED", "Hamyon shop credentials are missing", 503)

    async def create(self, payment_id: str, amount: int) -> dict:
        # Hamyon's documented endpoint accepts form-encoded shop_id/shop_key/amount/order_id.
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
                response = await client.post(self.base + "/payment/create", data={
                    "shop_id": self.shop_id, "shop_key": self.shop_key,
                    "amount": amount, "order_id": payment_id,
                })
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Unexpected payment response")
        except (httpx.HTTPError, ValueError) as exc:
            # A timeout can mean the invoice WAS created. Do not issue another create automatically.
            raise ServiceError("PAYMENT_SETUP_UNKNOWN", "Payment creation could not be confirmed", 502) from exc
        if response.status_code >= 400 or result.get("error") or not result.get("payment_id"):
            raise ServiceError("PAYMENT_REJECTED", "Payment provider rejected the invoice", 502)
        try:
            matches = str(result.get("order_id", payment_id)) == payment_id and int(result.get("amount", -1)) == amount
        except (ValueError, TypeError):
            matches = False
        if not matches:
            raise ServiceError("PAYMENT_MISMATCH", "Payment provider returned inconsistent invoice data", 502)
        return result

    async def status(self, provider_id: str) -> dict:
        # Public status endpoint documented as GET /payment/status?payment_id=...
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(self.base + "/payment/status", params={"payment_id": provider_id})
            result = response.json()
            if response.status_code >= 400 or not isinstance(result, dict) or result.get("error"):
                raise ValueError("Status not available")
            if isinstance(result.get("data"), dict) and not result.get("status"):
                result = result["data"]
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError("PAYMENT_STATUS_UNAVAILABLE", "Payment confirmation is temporarily unavailable", 503, True) from exc
