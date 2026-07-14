"""
Telegram mirror service for DC Atlas.

Handles adding Telegram sources and creating corresponding Delta Chat Channels.
"""

import logging
from typing import Optional

from ..storage.sqlite_storage import SQLiteStorage
from ..telegram.normalize import normalize_telegram_source

logger = logging.getLogger(__name__)


class TelegramMirrorService:
    """Business logic for Telegram mirror sources."""

    def __init__(self, catalog_service: any, adapter: any, storage: SQLiteStorage):
        self._catalog = catalog_service
        self._adapter = adapter
        self._storage = storage

    def _get_config(self):
        from ..config import get_config
        return get_config()

    def _resolve_user(self, user_id: str) -> int:
        """Resolve dc_contact_id to internal user ID, auto-creating if needed."""
        now = _now()
        user = self._storage.fetchone(
            "SELECT id FROM users WHERE dc_contact_id = ?", (user_id,)
        )
        if user:
            return user["id"]
        self._storage.execute(
            "INSERT INTO users (dc_contact_id, display_name, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, user_id, now, now),
        )
        return self._storage.fetchone(
            "SELECT id FROM users WHERE dc_contact_id = ?", (user_id,)
        )["id"]

    def add_source(self, raw_url: str, user_id: str) -> str:
        """Add a Telegram source and create a mirror channel if new."""
        # Normalize
        normalized = normalize_telegram_source(raw_url)
        if isinstance(normalized, str) and normalized.startswith("error_"):
            return _error_message(normalized)

        username = normalized

        # Check existing
        existing = self._storage.fetchone(
            "SELECT * FROM telegram_sources WHERE username = ?",
            (username,),
        )

        if existing:
            # Return existing mirror
            if existing.get("deltachat_invite_url"):
                return (
                    f"Этот Telegram-канал уже есть в каталоге.\n\n"
                    f"Источник: @{username}\n"
                    f"Delta Chat Channel: {existing.get('title', username)}\n"
                    f"Последняя проверка: {existing.get('last_checked_at', 'никогда')}\n\n"
                    f"Подписаться:\n{existing['deltachat_invite_url']}"
                )
            else:
                return (
                    f"Этот Telegram-канал уже в каталоге, но канал ещё не настроен.\n"
                    f"Статус: {existing.get('status', 'unknown')}"
                )

        # Try to get channel info (title, description) from Telegram page
        tg_title = None
        tg_description = None
        fetch_error = None
        try:
            from ..telegram.public_parser import TelegramPublicParser
            # We can't get title/desc from parser (it only gets posts),
            # so we try from the page itself via simple fetch
            import requests
            import re
            cfg = self._get_config()
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            if cfg.TELEGRAM_PROXY_ENABLED:
                pu = cfg.TELEGRAM_PROXY_URL
                session.trust_env = False
                session.proxies.update({"http": pu, "https": pu})
            base = cfg.TELEGRAM_PUBLIC_BASE_URL.rstrip('/').removesuffix('/s')
            verify_url = f"{base}/{username}"
            resp = session.get(verify_url, timeout=10)
            if resp.status_code == 200:
                html = resp.text
                m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
                if m:
                    tg_title = m.group(1)
                m = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html)
                if m:
                    tg_description = m.group(1)
        except Exception as e:
            logger.warning("Telegram fetch failed for @%s: %s", username, e)
            tg_title = None
            tg_description = None
            fetch_error = str(e)[:200]

        channel_title = tg_title or f"@{username} Mirror"
        channel_desc = tg_description or f"Зеркало Telegram-канала @{username}"

        # Create Delta Chat Channel
        try:
            channel = self._adapter.create_channel(channel_title)
            channel_id = channel.id
            invite_url = self._adapter.get_invite_link(channel_id)
            logger.info("Created channel %s for %s", channel_id, username)
        except Exception as e:
            logger.error("Failed to create channel for %s: %s", username, e)
            channel_id = None
            invite_url = None

        # Try to fetch and set avatar
        avatar_path = None
        avatar_status = "unknown"
        if channel_id:
            try:
                from ..config import get_config
                if get_config().AVATAR_FETCH_ENABLED:
                    from .avatar import AvatarService
                    av = AvatarService()
                    avatar_path = av.fetch_telegram_avatar(username)
                    if avatar_path:
                        try:
                            self._adapter.set_chat_image(channel_id, avatar_path)
                            avatar_status = "ok"
                            logger.info("Set avatar for %s", username)
                        except Exception:
                            avatar_status = "unsupported"
                    else:
                        avatar_status = "missing"
            except Exception as e:
                logger.debug("Avatar fetch failed for %s: %s", username, e)
                avatar_status = "failed"

        now = _now()

        # Create catalog item (no tags for telegram mirrors)
        db_user_id = self._resolve_user(user_id)
        item = self._catalog.create_item(
            item_type="telegram_mirror",
            title=channel_title,
            description=channel_desc,
            tags="",
            join_mode="open",
            invite_url=invite_url,
            source_ref=username,
            avatar_file_path=avatar_path,
            created_by_user_id=db_user_id,
        )

        # If DC Channel was not created, mark catalog item as pending_setup
        if not channel_id or not invite_url:
            self._storage.execute(
                "UPDATE catalog_items SET status = 'pending_setup', updated_at = ? WHERE id = ?",
                (now, item["id"]),
            )
            item["status"] = "pending_setup"

        # Create telegram source with avatar data
        self._storage.execute(
            """
            INSERT INTO telegram_sources
                (username, original_url, title, description, catalog_item_id,
                 deltachat_channel_id, deltachat_invite_url,
                 avatar_url, avatar_file_path, avatar_hash, avatar_status,
                 status, created_by_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                raw_url.strip(),
                channel_title,
                channel_desc,
                item["id"],
                channel_id,
                invite_url,
                None,  # avatar_url — not stored
                avatar_path,
                None,  # avatar_hash — not computed for now
                avatar_status,
                "active" if channel_id else "needs_manual_channel",
                db_user_id,
                now,
                now,
            ),
        )

        if invite_url:
            msg = (
                f"✅ Telegram-зеркало создано.\n"
                f"Источник: @{username}\n"
                f"Канал: {channel_title}\n\n"
                f"Подписаться:\n{invite_url}"
            )
            if fetch_error:
                msg += f"\n\n⚠️ Ошибка проверки: {fetch_error[:100]}"
            return msg
        else:
            msg = (
                f"⚠️ Источник @{username} добавлен, но канал не создан.\n"
                f"Статус: {item['status']}"
            )
            if fetch_error:
                msg += f"\n\n⚠️ Ошибка доступа: {fetch_error[:100]}"
            return msg


def _error_message(error_code: str) -> str:
    messages = {
        "error_private_or_invite_not_supported": (
            "Приватные приглашения не поддерживаются. Укажите публичный Telegram-канал."
        ),
        "error_invalid_username": "Некорректное имя Telegram-канала.",
        "error_empty": "Ссылка не указана.",
    }
    return messages.get(error_code, f"Ошибка: {error_code}")


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S")
