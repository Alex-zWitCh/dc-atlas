"""
Poller service for DC Atlas.

Periodically fetches new posts from active Telegram sources
and publishes them to corresponding Delta Chat Channels.
"""

import logging
import time
from typing import Optional

import requests

from ..config import get_config
from ..storage.sqlite_storage import SQLiteStorage
from ..telegram.public_parser import FetchError, TelegramPublicParser

logger = logging.getLogger(__name__)


class PollerResult:
    """Result of a single poller cycle."""

    def __init__(self):
        self.checked = 0
        self.published = 0
        self.retention_removed = 0
        self.errors = 0
        self.error_details: list[str] = []


class Poller:
    """Periodically checks Telegram sources and publishes new posts."""

    def __init__(
        self,
        storage: SQLiteStorage,
        adapter: any,
        parser: Optional[TelegramPublicParser] = None,
    ):
        self._storage = storage
        self._adapter = adapter
        self._parser = parser or TelegramPublicParser()
        self._max_per_cycle = get_config().POLL_MAX_SOURCES_PER_CYCLE
        self._cfg = get_config()

    def run_once(self) -> PollerResult:
        """Run one poll cycle. Fetches and publishes new posts."""
        result = PollerResult()

        sources = self._storage.fetchall(
            """
            SELECT * FROM telegram_sources
            WHERE status = 'active' AND deltachat_channel_id IS NOT NULL
            ORDER BY last_checked_at ASC NULLS FIRST
            LIMIT ?
            """,
            (self._max_per_cycle,),
        )

        if not sources:
            logger.debug("No active telegram sources to poll")
            return result

        for source in sources:
            result.checked += 1
            try:
                self._poll_source(source, result)
            except Exception as e:
                result.errors += 1
                msg = f"{source['username']}: {e}"
                result.error_details.append(msg)
                logger.warning("Poll error for %s: %s", source["username"], e)
                self._update_source_error(source["id"], str(e))

        # Run retention cleanup after each poll cycle
        try:
            from .retention import RetentionService
            ret_stats = RetentionService(self._storage).run_once()
            result.retention_removed = (
                ret_stats.get("removed_by_age", 0)
                + ret_stats.get("removed_by_count", 0)
            )
        except Exception as e:
            logger.debug("Retention cleanup error: %s", e)

        # Retry avatar fetch for sources with missing/failed avatars
        self._retry_failed_avatars()

        logger.info(
            "Polled %d sources, published %d, retention %d, errors %d",
            result.checked,
            result.published,
            result.retention_removed,
            result.errors,
        )
        return result

    def _poll_source(self, source: dict, result: PollerResult) -> None:
        """Check a single source and publish new posts."""
        username = source["username"]
        channel_id = source.get("deltachat_channel_id")
        last_id = source.get("last_post_id", 0) or 0

        now = _now()
        self._storage.execute(
            "UPDATE telegram_sources SET last_checked_at = ? WHERE id = ?",
            (now, source["id"]),
        )

        try:
            posts = self._parser.fetch_channel(username)
        except FetchError as e:
            self._update_source_error(source["id"], str(e))
            return

        # Clear stale fetch errors after a successful fetch
        self._clear_fetch_error_if_any(source["id"])

        if not posts:
            return

        new_posts = [p for p in posts if p.post_id > last_id]
        if not new_posts:
            return

        new_posts.sort(key=lambda p: p.post_id)

        published_ids = []
        had_publish_error = False
        for post in new_posts:
            try:
                self._publish_post(source, post, channel_id, result)
            except Exception as e:
                logger.error(
                    "Failed to publish post %d for %s: %s",
                    post.post_id,
                    username,
                    e,
                )
                had_publish_error = True
                self._save_failed(source["id"], post, str(e))
                self._storage.execute(
                    "UPDATE telegram_sources SET error_message = ?, updated_at = ? WHERE id = ?",
                    (f"publish failed: {str(e)[:450]}", _now(), source["id"]),
                )
                break  # Stop on first error, don't skip remaining posts

            self._save_published(source["id"], post)
            published_ids.append(post.post_id)
            result.published += 1

        if published_ids:
            max_id = max(published_ids)
            self._storage.execute(
                "UPDATE telegram_sources SET last_post_id = ?, last_success_at = ?, updated_at = ? WHERE id = ?",
                (max_id, now, now, source["id"]),
            )

        # Clear error state only if no publish errors occurred
        if not had_publish_error:
            self._storage.execute(
                "UPDATE telegram_sources SET consecutive_errors = 0, error_message = NULL WHERE id = ?",
                (source["id"],),
            )

    def _retry_failed_avatars(self) -> None:
        """Retry avatar fetch for sources with missing/failed avatars."""
        cfg = get_config()
        if not cfg.AVATAR_FETCH_ENABLED:
            return

        sources = self._storage.fetchall(
            """
            SELECT * FROM telegram_sources
            WHERE status = 'active'
              AND deltachat_channel_id IS NOT NULL
              AND (avatar_status IS NULL OR avatar_status IN ('unknown', 'failed', 'missing'))
            LIMIT 5
            """
        )
        if not sources:
            return

        try:
            from .avatar import AvatarService
            av = AvatarService()
        except Exception as e:
            logger.debug("Could not init AvatarService: %s", e)
            return

        for source in sources:
            username = source["username"]
            channel_id = source.get("deltachat_channel_id")
            if not channel_id:
                continue
            try:
                avatar_path = av.fetch_telegram_avatar(username)
                now = _now()
                if avatar_path:
                    try:
                        self._adapter.set_chat_image(int(channel_id), avatar_path)
                        self._storage.execute(
                            """UPDATE telegram_sources
                               SET avatar_file_path=?, avatar_status='ok',
                                   avatar_updated_at=?, updated_at=?
                               WHERE id=?""",
                            (avatar_path, now, now, source["id"]),
                        )
                        logger.info("Avatar set for %s", username)
                    except Exception:
                        self._storage.execute(
                            """UPDATE telegram_sources
                               SET avatar_status='unsupported', updated_at=?
                               WHERE id=?""",
                            (now, source["id"]),
                        )
                else:
                    self._storage.execute(
                        """UPDATE telegram_sources
                           SET avatar_status='missing', avatar_checked_at=?, updated_at=?
                           WHERE id=?""",
                        (now, now, source["id"]),
                    )
            except Exception as e:
                logger.debug("Avatar retry failed for %s: %s", username, e)
                self._storage.execute(
                    """UPDATE telegram_sources
                       SET avatar_status='failed', updated_at=?
                       WHERE id=?""",
                    (_now(), source["id"]),
                )

    def _download_photos(self, photo_urls):
        """Download photo files to local temp directory."""
        import time as _time
        from pathlib import Path
        paths = []
        media_dir = Path(self._cfg.TELEGRAM_TEMP_MEDIA_DIR)
        media_dir.mkdir(parents=True, exist_ok=True)
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if self._cfg.TELEGRAM_PROXY_ENABLED:
            pu = self._cfg.TELEGRAM_PROXY_URL
            session.trust_env = False
            session.proxies.update({"http": pu, "https": pu})
        for url in photo_urls[:self._cfg.TELEGRAM_MAX_PHOTOS_PER_POST]:
            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                ct = resp.headers.get("Content-Type", "")
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"
                else:
                    ext = ".jpg"
                fname = "photo_%d_%d%s" % (_time.time(), len(paths), ext)
                fpath = media_dir / fname
                fpath.write_bytes(resp.content)
                paths.append(str(fpath))
            except Exception as e:
                logger.debug("Failed to download photo %s: %s", url, e)
        return paths

    def _publish_post(self, source, post, channel_id, result):
        """Publish a single post with proper media handling."""
        username = source["username"]
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            cid = channel_id
        fetch_media = self._cfg.TELEGRAM_FETCH_MEDIA

        if not fetch_media:
            self._adapter.publish_to_channel(cid, self._format_post(username, post))
            return

        header = "Источник: @%s\nПост: %s\n" % (username, post.original_url)
        body = ("\n" + post.text) if post.text else ""

        # --- Handle video posts ---
        if post.has_video and post.video_thumb_urls:
            thumb_paths = []
            try:
                thumb_paths = self._download_photos(post.video_thumb_urls[:1])
                if thumb_paths:
                    video_text = header + body + "\n\n▶️ Видео\n%s" % post.original_url
                    self._adapter.publish_to_channel(cid, video_text, [thumb_paths[0]])
                else:
                    self._adapter.publish_to_channel(cid, header + body + "\n\n▶️ Видео\n%s" % post.original_url)
                extra_videos = post.total_video_count - 1
                if extra_videos > 0:
                    self._adapter.publish_to_channel(
                        cid, "▶️ Ещё %d видео — %s" % (extra_videos, post.original_url)
                    )
            finally:
                self._cleanup_temp_files(thumb_paths)
            return

        # --- Handle photo posts ---
        if post.photo_urls:
            max_photos = self._cfg.TELEGRAM_MAX_PHOTOS_PER_POST
            photos_to_attach = post.photo_urls[:max_photos]
            extra_photos = post.total_photo_count - max_photos

            downloaded = []
            try:
                downloaded = self._download_photos(photos_to_attach)
                if downloaded:
                    caption = header + body
                    self._adapter.publish_to_channel(cid, caption, [downloaded[0]])
                    for fpath in downloaded[1:]:
                        self._adapter.publish_to_channel(cid, "", [fpath])
                else:
                    self._adapter.publish_to_channel(cid, header + body)

                if extra_photos > 0:
                    self._adapter.publish_to_channel(
                        cid, "📸 Ещё %d фото — %s" % (extra_photos, post.original_url)
                    )
            finally:
                self._cleanup_temp_files(downloaded)
            return

        # --- Plain text post ---
        self._adapter.publish_to_channel(cid, self._format_post(username, post))

    def _cleanup_temp_files(self, paths: list[str]) -> None:
        """Remove temporary downloaded media files."""
        from pathlib import Path
        for fpath in paths or []:
            try:
                Path(fpath).unlink(missing_ok=True)
            except Exception as e:
                logger.debug("Failed to remove temp media %s: %s", fpath, e)

    def _format_post(self, username: str, post) -> str:
        """Format a Telegram post for Delta Chat Channel (text-only fallback)."""
        lines = [f"Источник: @{username}", f"Пост: {post.original_url}", ""]
        if post.text:
            lines.append(post.text)
        # Only add media note when NOT fetching media
        if not self._cfg.TELEGRAM_FETCH_MEDIA:
            parts = []
            if post.has_video:
                parts.append("Видео")
            if post.has_photo:
                parts.append("Фото")
            if post.has_file:
                parts.append("Файлы")
            if parts:
                lines.append("")
                lines.append("Медиа: " + ", ".join(parts) + " — в оригинале")
        return "\n".join(lines)

    def _save_published(self, source_id: int, post) -> None:
        """Save published post to prevent duplicates."""
        import hashlib

        text_hash = hashlib.md5((post.text or "").encode()).hexdigest()

        self._storage.execute(
            """
            INSERT INTO telegram_posts
                (source_id, telegram_post_id, text_hash, text, original_url,
                 has_photo, photo_count, has_video, video_count, has_file,
                 publish_status, error_message, published_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', NULL, ?, ?)
            ON CONFLICT(source_id, telegram_post_id) DO UPDATE SET
                text_hash = excluded.text_hash,
                text = excluded.text,
                original_url = excluded.original_url,
                has_photo = excluded.has_photo,
                photo_count = excluded.photo_count,
                has_video = excluded.has_video,
                video_count = excluded.video_count,
                has_file = excluded.has_file,
                publish_status = 'published',
                error_message = NULL,
                published_at = excluded.published_at
            """,
            (
                source_id,
                post.post_id,
                text_hash,
                post.text or "",
                post.original_url,
                1 if post.has_photo else 0,
                post.total_photo_count or len(getattr(post, 'photo_urls', []) or []),
                1 if post.has_video else 0,
                post.total_video_count or 0,
                1 if post.has_file else 0,
                _now(),
                _now(),
            ),
        )

    def _save_failed(self, source_id: int, post, error: str) -> None:
        """Save a post that failed to publish."""
        import hashlib

        text_hash = hashlib.md5((post.text or "").encode()).hexdigest()

        self._storage.execute(
            """
            INSERT INTO telegram_posts
                (source_id, telegram_post_id, text_hash, text, original_url,
                 has_photo, photo_count, has_video, video_count, has_file,
                 publish_status, error_message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'publish_failed', ?, ?)
            ON CONFLICT(source_id, telegram_post_id) DO UPDATE SET
                text_hash = excluded.text_hash,
                text = excluded.text,
                original_url = excluded.original_url,
                has_photo = excluded.has_photo,
                photo_count = excluded.photo_count,
                has_video = excluded.has_video,
                video_count = excluded.video_count,
                has_file = excluded.has_file,
                publish_status = 'publish_failed',
                error_message = excluded.error_message
            """,
            (
                source_id,
                post.post_id,
                text_hash,
                post.text or "",
                post.original_url,
                1 if post.has_photo else 0,
                post.total_photo_count or len(getattr(post, 'photo_urls', []) or []),
                1 if post.has_video else 0,
                post.total_video_count or 0,
                1 if post.has_file else 0,
                error[:500],
                _now(),
            ),
        )

    def _clear_fetch_error_if_any(self, source_id: int) -> None:
        """Clear stale fetch errors after a successful Telegram fetch.

        Do not clear active publish errors because fetch success does not mean
        Delta Chat publishing is healthy.
        """
        row = self._storage.fetchone(
            "SELECT error_message FROM telegram_sources WHERE id = ?",
            (source_id,),
        )
        err = (row["error_message"] or "") if row else ""

        if err.startswith("publish failed:"):
            self._storage.execute(
                "UPDATE telegram_sources SET consecutive_errors = 0 WHERE id = ?",
                (source_id,),
            )
            return

        self._storage.execute(
            "UPDATE telegram_sources SET consecutive_errors = 0, error_message = NULL WHERE id = ?",
            (source_id,),
        )

    def _update_source_error(self, source_id: int, error: str) -> None:
        """Increment error counter; disable source after max consecutive errors."""
        max_errors = get_config().TELEGRAM_MAX_CONSECUTIVE_ERRORS
        self._storage.execute(
            "UPDATE telegram_sources SET consecutive_errors = consecutive_errors + 1, error_message = ? WHERE id = ?",
            (error[:500], source_id),
        )
        row = self._storage.fetchone(
            "SELECT consecutive_errors FROM telegram_sources WHERE id = ?", (source_id,)
        )
        if row and row["consecutive_errors"] >= max_errors:
            self._storage.execute(
                "UPDATE telegram_sources SET status = 'fetch_error' WHERE id = ?",
                (source_id,),
            )
            logger.warning(
                "Source %d disabled after %d consecutive errors: %s",
                source_id, max_errors, error[:100],
            )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
