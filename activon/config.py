"""Environment-only bootstrap settings. Runtime store settings live in MySQL."""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    demo: bool = os.getenv("APP_DEMO", "0") == "1"
    database_url: str = os.getenv("DATABASE_URL", "")
    bot_token: str = os.getenv("BOT_TOKEN", "")
    webapp_url: str = os.getenv("WEBAPP_URL", "")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    encryption_key: str = os.getenv("FIELD_ENCRYPTION_KEY", "")
    admin_ids_raw: str = os.getenv("ADMIN_TELEGRAM_IDS", "")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_ids_raw.split(",") if x.strip().isdigit()}

    def validate(self) -> None:
        if self.demo:
            return
        missing = [name for name, value in (
            ("DATABASE_URL", self.database_url), ("BOT_TOKEN", self.bot_token),
            ("WEBAPP_URL", self.webapp_url), ("SESSION_SECRET", self.session_secret),
            ("FIELD_ENCRYPTION_KEY", self.encryption_key), ("ADMIN_TELEGRAM_IDS", self.admin_ids_raw),
        ) if not value]
        if missing:
            raise RuntimeError("Missing production settings: " + ", ".join(missing))
        if not self.database_url.startswith("mysql+pymysql://"):
            raise RuntimeError("Production requires a MySQL URL (mysql+pymysql://...)")
        host = urlparse(self.webapp_url).hostname
        if not self.webapp_url.startswith("https://") or not host or host.endswith(".example"):
            raise RuntimeError("Telegram WebApp requires a real public HTTPS WEBAPP_URL")
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{30,}", self.bot_token):
            raise RuntimeError("BOT_TOKEN must be a valid Telegram bot token")
        if len(self.session_secret) < 32 or self.session_secret.startswith("REPLACE_"):
            raise RuntimeError("SESSION_SECRET must be a newly generated secret (32+ characters)")
        if not self.admin_ids:
            raise RuntimeError("ADMIN_TELEGRAM_IDS must include a Telegram user id")
        try:
            from cryptography.fernet import Fernet
            Fernet(self.encryption_key.encode())
        except (ValueError, TypeError):
            raise RuntimeError("FIELD_ENCRYPTION_KEY must be a valid Fernet key") from None


config = Config()
config.validate()
