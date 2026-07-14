"""
Avatar service for DC Atlas.

Fetches, caches, and manages avatars for Telegram mirrors.
No binary data stored in SQLite — only file paths and metadata.
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

from ..config import get_config

logger = logging.getLogger(__name__)


class AvatarService:
    """Fetch and cache avatars from Telegram channel pages."""

    def __init__(self, cache_dir: Optional[str] = None):
        cfg = get_config()
        self._cache_dir = Path(
            cache_dir or cfg.APP_DATA_DIR / "avatars" / "telegram"
        )
        self._max_bytes = cfg.AVATAR_MAX_BYTES
        self._timeout = cfg.AVATAR_HTTP_TIMEOUT_SECONDS
        self._allowed_mime = cfg.AVATAR_ALLOWED_MIME
        self._refresh_hours = cfg.AVATAR_REFRESH_INTERVAL_HOURS
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        # Apply proxy if configured
        if cfg.TELEGRAM_PROXY_ENABLED:
            pu = cfg.TELEGRAM_PROXY_URL
            self._session.trust_env = False
            self._session.proxies.update({"http": pu, "https": pu})

    def fetch_telegram_avatar(self, username: str) -> Optional[str]:
        """
        Try to get the avatar for a Telegram channel.
        Returns local file path or None.
        """
        avatar_url = self._extract_avatar_url(username)
        if not avatar_url:
            return None
        return self._download(avatar_url, username)

    def _extract_avatar_url(self, username: str) -> Optional[str]:
        """Extract avatar URL from Telegram channel page."""
        from ..config import get_config
        cfg = get_config()
        base = cfg.TELEGRAM_PUBLIC_BASE_URL.rstrip('/').removesuffix('/s')
        url = f"{base}/{username}"
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("Failed to fetch page for %s: %s", username, e)
            return None

        html = resp.text
        # Try og:image first
        import re
        m = re.search(
            r'<meta\s+property="og:image"\s+content="([^"]+)"',
            html,
        )
        if m:
            return m.group(1)
        # Try twitter:image
        m = re.search(
            r'<meta\s+name="twitter:image"\s+content="([^"]+)"',
            html,
        )
        if m:
            return m.group(1)
        return None

    def _download(self, avatar_url: str, username: str) -> Optional[str]:
        """Download avatar, validate, save to cache."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            resp = self._session.get(avatar_url, timeout=self._timeout, stream=True)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("Failed to download avatar for %s: %s", username, e)
            return None

        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type not in self._allowed_mime:
            logger.debug(
                "Unsupported MIME for %s: %s", username, content_type
            )
            return None

        data = resp.content
        if len(data) > self._max_bytes:
            logger.debug("Avatar too large for %s: %d bytes", username, len(data))
            return None

        # Determine extension
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(content_type, ".jpg")

        file_path = self._cache_dir / f"{username}{ext}"
        file_path.write_bytes(data)
        logger.debug("Saved avatar for %s: %s (%d bytes)", username, file_path, len(data))
        return str(file_path)

    def compute_hash(self, file_path: str) -> str:
        """SHA-256 hash of file contents."""
        h = hashlib.sha256()
        h.update(Path(file_path).read_bytes())
        return h.hexdigest()

    def remove_old(self, file_path: str) -> None:
        """Remove a cached avatar file."""
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Failed to remove old avatar %s: %s", file_path, e)

    def needs_refresh(self, checked_at: Optional[str]) -> bool:
        """Check if enough time passed since last avatar check."""
        if not checked_at:
            return True
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(checked_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
            return elapsed >= self._refresh_hours * 3600
        except Exception:
            return True
