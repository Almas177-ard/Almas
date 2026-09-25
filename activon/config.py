"""Environment-only bootstrap settings. Runtime store settings live in MySQL."""
from __future__ import annotations

import os
import re
from urllib.parse import quote_plus, urlparse
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    demo: bool = os.getenv("APP_DEMO", "0") == "1"
    database_url: str = os.getenv("DATABASE_URL", "")
    # Separate MySQL credentials (hosting panels hand these out as individual
    # fields). Used only when DATABASE_URL is empty. Root is NOT required:
    # any MySQL user with full rights on its own database works.
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: str = os.getenv("DB_PORT", "3306")
    db_name: str = os.getenv("DB_NAME", "")
    db_user: str = os.getenv("DB_USER", "")
    db_password: str = os.getenv("DB_PASSWORD", "")
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

    @property
    def sqlalchemy_url(self) -> str:
        """Effective SQLAlchemy URL.

        An explicit DATABASE_URL wins; otherwise the URL is built from the
        DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD parts. User and password
        are URL-encoded, so any panel-generated password works as-is.
        """
        if self.database_url:
            return self.database_url
        if self.db_name and self.db_user:
            auth = quote_plus(self.db_user)
            if self.db_password:
                auth += ":" + quote_plus(self.db_password)
            host = self.db_host or "127.0.0.1"
            port = self.db_port or "3306"
            return f"mysql+pymysql://{auth}@{host}:{port}/{self.db_name}?charset=utf8mb4"
        return ""

    def validate(self) -> None:
        if self.demo:
            return
        missing = [name for name, value in (
            ("BOT_TOKEN", self.bot_token),
            ("WEBAPP_URL", self.webapp_url), ("SESSION_SECRET", self.session_secret),
            ("FIELD_ENCRYPTION_KEY", self.encryption_key), ("ADMIN_TELEGRAM_IDS", self.admin_ids_raw),
        ) if not value]
        db_url = self.sqlalchemy_url
        if not db_url:
            missing.append("DB_HOST/DB_NAME/DB_USER/DB_PASSWORD (or DATABASE_URL)")
        if missing:
            raise RuntimeError("Missing production settings: " + ", ".join(missing))
        if not db_url.startswith("mysql+pymysql://"):
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
