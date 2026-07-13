"""
Configuration module for DC Atlas bot.

Loads settings from environment variables with fallback to defaults.
Uses python-dotenv when available.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class Config:
    """Immutable configuration container loaded from environment."""

    def __init__(self):
        self.APP_ENV = os.getenv("APP_ENV", "development")
        self.APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/var/lib/dc-atlas"))
        self.APP_DB_PATH = Path(
            os.getenv("APP_DB_PATH", str(self.APP_DATA_DIR / "dc_atlas.sqlite3"))
        )
        self.APP_LOG_DIR = Path(os.getenv("APP_LOG_DIR", "/var/log/dc-atlas"))

        self.BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "DC Atlas")
        self.BOT_LANGUAGE = os.getenv("BOT_LANGUAGE", "ru")

        self.POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
        self.POLL_MAX_SOURCES_PER_CYCLE = int(
            os.getenv("POLL_MAX_SOURCES_PER_CYCLE", "100")
        )
        self.POLL_HTTP_TIMEOUT_SECONDS = int(
            os.getenv("POLL_HTTP_TIMEOUT_SECONDS", "15")
        )

        self.TELEGRAM_PUBLIC_BASE_URL = os.getenv(
            "TELEGRAM_PUBLIC_BASE_URL", "https://t.me/s"
        )
        self.TELEGRAM_MAX_PHOTOS_PER_POST = int(
            os.getenv("TELEGRAM_MAX_PHOTOS_PER_POST", "3")
        )
        self.TELEGRAM_FETCH_MEDIA = (
            os.getenv("TELEGRAM_FETCH_MEDIA", "false").lower() == "true"
        )
        # Optional HTTP proxy for Telegram (bypass Russia block)
        self.TELEGRAM_PROXY_ENABLED = (
            os.getenv("TELEGRAM_PROXY_ENABLED", "false").lower() == "true"
        )
        self.TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

        if self.TELEGRAM_PROXY_ENABLED and not self.TELEGRAM_PROXY_URL:
            raise ValueError(
                "TELEGRAM_PROXY_ENABLED=true, but TELEGRAM_PROXY_URL is empty"
            )

        # Media and retention settings
        self.TELEGRAM_TEMP_MEDIA_DIR = os.getenv(
            "TELEGRAM_TEMP_MEDIA_DIR", "/tmp/dc-atlas-media"
        )
        self.TELEGRAM_MAX_CONSECUTIVE_ERRORS = int(
            os.getenv("TELEGRAM_MAX_CONSECUTIVE_ERRORS", "5")
        )
        self.TELEGRAM_STORE_MEDIA_BINARY = (
            os.getenv("TELEGRAM_STORE_MEDIA_BINARY", "false").lower() == "true"
        )
        self.TELEGRAM_STORE_FULL_TEXT = (
            os.getenv("TELEGRAM_STORE_FULL_TEXT", "true").lower() == "true"
        )
        self.TELEGRAM_POST_RETENTION_DAYS = int(
            os.getenv("TELEGRAM_POST_RETENTION_DAYS", "30")
        )
        self.TELEGRAM_POST_RETENTION_MAX_PER_SOURCE = int(
            os.getenv("TELEGRAM_POST_RETENTION_MAX_PER_SOURCE", "500")
        )

        # Avatar settings
        self.AVATAR_FETCH_ENABLED = (
            os.getenv("AVATAR_FETCH_ENABLED", "true").lower() == "true"
        )
        self.AVATAR_MAX_BYTES = int(os.getenv("AVATAR_MAX_BYTES", "1048576"))
        self.AVATAR_HTTP_TIMEOUT_SECONDS = int(
            os.getenv("AVATAR_HTTP_TIMEOUT_SECONDS", "10")
        )
        self.AVATAR_CACHE_DIR = os.getenv(
            "AVATAR_CACHE_DIR", str(self.APP_DATA_DIR / "avatars")
        )
        self.AVATAR_REFRESH_INTERVAL_HOURS = int(
            os.getenv("AVATAR_REFRESH_INTERVAL_HOURS", "24")
        )
        self.AVATAR_ALLOWED_MIME = (
            os.getenv(
                "AVATAR_ALLOWED_MIME",
                "image/jpeg,image/png,image/webp",
            )
            .split(",")
        )
        self.BOT_AVATAR_PATH = os.getenv(
            "BOT_AVATAR_PATH",
            str(self.APP_DATA_DIR / "avatars" / "defaults" / "bot.png"),
        )

        self.CATALOG_AUTO_APPROVE = (
            os.getenv("CATALOG_AUTO_APPROVE", "true").lower() == "true"
        )
        self.REPORTS_TO_HIDE = int(os.getenv("REPORTS_TO_HIDE", "5"))

        self.DELTA_CHAT_PROFILE_PATH = Path(
            os.getenv(
                "DELTA_CHAT_PROFILE_PATH",
                str(self.APP_DATA_DIR / "deltachat-profile"),
            )
        )

        # Delta Chat account credentials
        self.DC_EMAIL = os.getenv("DC_EMAIL", "")
        self.DC_PASSWORD = os.getenv("DC_PASSWORD", "")

        # Optional custom IMAP/SMTP servers (override chatmail domain)
        self.DC_IMAP_SERVER = os.getenv("DC_IMAP_SERVER", "")
        self.DC_SMTP_SERVER = os.getenv("DC_SMTP_SERVER", "")

        # Support contact shown in help/welcome messages
        self.SUPPORT_INVITE_URL = os.getenv("SUPPORT_INVITE_URL", "")

        # Delta Chat local profile cleanup
        self.DC_PROFILE_CLEANUP_ENABLED = (
            os.getenv("DC_PROFILE_CLEANUP_ENABLED", "true").lower() == "true"
        )
        self.DC_PROFILE_CLEANUP_DAYS = int(os.getenv("DC_PROFILE_CLEANUP_DAYS", "7"))
        self.DC_PROFILE_CLEANUP_INTERVAL_SECONDS = int(
            os.getenv("DC_PROFILE_CLEANUP_INTERVAL_SECONDS", "3600")
        )

        # Admin accounts (comma-separated email addresses)
        raw_admins = os.getenv("BOT_ADMIN_EMAILS", "")
        self.BOT_ADMIN_EMAILS = {e.strip() for e in raw_admins.split(",") if e.strip()}

    def __repr__(self):
        proxy_status = (
            f"proxy={'ON' if self.TELEGRAM_PROXY_ENABLED else 'OFF'}"
        )
        dc_status = "dc=configured" if self.DC_EMAIL else "dc=no-account"
        return f"Config(env={self.APP_ENV}, db={self.APP_DB_PATH}, {proxy_status}, {dc_status})"


# Singleton instance
_config = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
