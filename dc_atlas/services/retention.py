"""
Retention service for DC Atlas.

Periodically cleans up old Telegram posts from SQLite
according to configurable age and count limits.
Does not modify telegram_sources.last_post_id.
"""

import logging
from datetime import datetime, timedelta, timezone

from ..config import get_config
from ..storage.sqlite_storage import SQLiteStorage

logger = logging.getLogger(__name__)


class RetentionService:
    """
    Cleans up old telegram_posts entries based on retention policy.

    - Removes posts older than TELEGRAM_POST_RETENTION_DAYS
    - Limits posts per source to TELEGRAM_POST_RETENTION_MAX_PER_SOURCE
    - Does NOT touch telegram_sources.last_post_id
    """

    def __init__(self, storage: SQLiteStorage):
        self._storage = storage
        cfg = get_config()
        self._retention_days = cfg.TELEGRAM_POST_RETENTION_DAYS
        self._max_per_source = cfg.TELEGRAM_POST_RETENTION_MAX_PER_SOURCE

    def run_once(self) -> dict:
        """Run one cleanup cycle. Returns stats dict."""
        stats = {
            "removed_by_age": 0,
            "removed_by_count": 0,
            "errors": 0,
        }

        try:
            stats["removed_by_age"] = self._remove_by_age()
        except Exception as e:
            logger.error("Retention by age failed: %s", e)
            stats["errors"] += 1

        try:
            stats["removed_by_count"] = self._remove_excess_per_source()
        except Exception as e:
            logger.error("Retention by count failed: %s", e)
            stats["errors"] += 1

        total = stats["removed_by_age"] + stats["removed_by_count"]
        if total > 0:
            logger.info(
                "Retention: removed %d posts (age=%d, excess=%d)",
                total,
                stats["removed_by_age"],
                stats["removed_by_count"],
            )
        return stats

    def _remove_by_age(self) -> int:
        """Remove posts older than retention_days."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")

        result = self._storage.execute(
            "DELETE FROM telegram_posts WHERE created_at < ?",
            (cutoff,),
        )
        # rowcount is not reliably supported by all sqlite wrappers
        affected = self._storage.fetchone(
            "SELECT changes() as cnt"
        )["cnt"]
        return affected

    def _remove_excess_per_source(self) -> int:
        """For each source, keep only the last max_per_source posts."""
        total_removed = 0

        sources = self._storage.fetchall(
            "SELECT DISTINCT source_id FROM telegram_posts"
        )
        for src in sources:
            sid = src["source_id"]
            count_row = self._storage.fetchone(
                "SELECT COUNT(*) as cnt FROM telegram_posts WHERE source_id = ?",
                (sid,),
            )
            count = count_row["cnt"] if count_row else 0
            if count <= self._max_per_source:
                continue

            excess = count - self._max_per_source
            # Delete oldest excess rows for this source
            self._storage.execute(
                """
                DELETE FROM telegram_posts
                WHERE id IN (
                    SELECT id FROM telegram_posts
                    WHERE source_id = ?
                    ORDER BY id ASC
                    LIMIT ?
                )
                """,
                (sid, excess),
            )
            removed = self._storage.fetchone("SELECT changes() as cnt")["cnt"]
            total_removed += removed

        return total_removed
