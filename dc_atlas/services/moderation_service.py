"""
Moderation service for DC Atlas.

Handles user reports and automatic moderation actions.
"""

import time
from typing import Optional

from ..config import get_config
from ..storage.sqlite_storage import SQLiteStorage


class ModerationService:
    """Business logic for reports and moderation."""

    def __init__(self, storage: SQLiteStorage):
        self._storage = storage
        self.hide_threshold = get_config().REPORTS_TO_HIDE

    def _resolve_user_db_id(self, reporter_user_id: Optional[str]) -> Optional[int]:
        """Resolve dc_contact_id to internal users.id."""
        if not reporter_user_id:
            return None
        user = self._storage.fetchone(
            "SELECT id FROM users WHERE dc_contact_id = ?",
            (reporter_user_id,),
        )
        if user:
            return user["id"]
        now = _now()
        self._storage.execute(
            "INSERT INTO users (dc_contact_id, display_name, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (reporter_user_id, reporter_user_id, now, now),
        )
        return self._storage.fetchone(
            "SELECT id FROM users WHERE dc_contact_id = ?",
            (reporter_user_id,),
        )["id"]

    def can_report(self, reporter_user_id: str) -> Optional[str]:
        """Check if user can send any report today. 1 report per day total."""
        user_db_id = self._resolve_user_db_id(reporter_user_id)
        if not user_db_id:
            return None
        row = self._storage.fetchone(
            """SELECT id FROM reports
               WHERE reporter_user_id = ?
               AND created_at > datetime('now', '-1 day')""",
            (user_db_id,),
        )
        if row:
            return "Вы уже отправляли жалобу сегодня. Следующая жалоба возможна через 24 часа."
        return None

    def create_report(
        self,
        catalog_item_id: int,
        reporter_user_id: Optional[str],
        reason: str,
    ) -> dict:
        """Create a report for a catalog item.

        Returns dict with:
            report_id: int — ID created report
            auto_hidden: bool — whether auto-hide was triggered
            unique_count: int — total unique ACTIVE reporters for this item
        """
        user_db_id = self._resolve_user_db_id(reporter_user_id)

        row = self._storage.execute_returning(
            """INSERT INTO reports (catalog_item_id, reporter_user_id, reason, created_at)
               VALUES (?, ?, ?, ?) RETURNING id""",
            (catalog_item_id, user_db_id, reason, _now()),
        )
        report_id = row["id"]

        # Count unique ACTIVE reporters only (status='new')
        cnt_row = self._storage.fetchone(
            """SELECT COUNT(DISTINCT reporter_user_id) as cnt
               FROM reports
               WHERE catalog_item_id = ? AND status = 'new'""",
            (catalog_item_id,),
        )
        unique_count = cnt_row["cnt"] if cnt_row else 0

        auto_hidden = unique_count >= self.hide_threshold
        if auto_hidden:
            self._storage.execute(
                "UPDATE catalog_items SET status = 'hidden_pending_review', updated_at = ? WHERE id = ?",
                (_now(), catalog_item_id),
            )

        return {
            "report_id": report_id,
            "auto_hidden": auto_hidden,
            "unique_count": unique_count,
        }

    def get_active_report_count(self, catalog_item_id: int) -> int:
        """Get unique active reporters for an item (status='new')."""
        row = self._storage.fetchone(
            """SELECT COUNT(DISTINCT reporter_user_id) as cnt
               FROM reports
               WHERE catalog_item_id = ? AND status = 'new'""",
            (catalog_item_id,),
        )
        return row["cnt"] if row else 0

    def dismiss_report(self, report_id: int) -> bool:
        """Deactivate a single report. Returns True if found."""
        self._storage.execute(
            "UPDATE reports SET status = 'dismissed' WHERE id = ?",
            (report_id,),
        )
        return True

    def dismiss_reports_for_item(self, catalog_item_id: int) -> int:
        """Deactivate all active reports for an item. Returns count."""
        cur = self._storage.execute(
            "UPDATE reports SET status = 'dismissed' WHERE catalog_item_id = ? AND status = 'new'",
            (catalog_item_id,),
        )
        return cur.rowcount

    def dismiss_reports_by_user_email(self, email: str) -> int:
        """Deactivate all active reports from a user by email. Returns count."""
        user = self._storage.fetchone(
            "SELECT id FROM users WHERE dc_contact_id = ?", (email,)
        )
        if not user:
            return 0
        cur = self._storage.execute(
            "UPDATE reports SET status = 'dismissed' WHERE reporter_user_id = ? AND status = 'new'",
            (user["id"],),
        )
        return cur.rowcount

    def get_reports(
        self, catalog_item_id: Optional[int] = None, limit: int = 20
    ) -> list[dict]:
        """Get reports, optionally filtered by item. Shows all statuses."""
        if catalog_item_id:
            return self._storage.fetchall(
                """SELECT r.id, r.catalog_item_id, u.dc_contact_id as reporter,
                          r.reason, r.status, r.created_at
                   FROM reports r
                   LEFT JOIN users u ON u.id = r.reporter_user_id
                   WHERE r.catalog_item_id = ?
                   ORDER BY r.created_at DESC LIMIT ?""",
                (catalog_item_id, limit),
            )
        return self._storage.fetchall(
            """SELECT r.id, r.catalog_item_id, u.dc_contact_id as reporter,
                      r.reason, r.status, r.created_at
               FROM reports r
               LEFT JOIN users u ON u.id = r.reporter_user_id
               ORDER BY r.created_at DESC LIMIT ?""",
            (limit,),
        )

    def admin_hide(self, item_id: int, reason: str) -> None:
        """Admin action: hide a catalog item."""
        self._storage.execute(
            "UPDATE catalog_items SET status = 'hidden_by_admin', updated_at = ? WHERE id = ?",
            (_now(), item_id),
        )

    def admin_show(self, item_id: int) -> None:
        """Admin action: restore a hidden item."""
        self._storage.execute(
            "UPDATE catalog_items SET status = 'active', updated_at = ? WHERE id = ?",
            (_now(), item_id),
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
