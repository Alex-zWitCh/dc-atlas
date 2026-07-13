"""
Catalog service for DC Atlas.

Handles creation, search, and retrieval of catalog items.
"""

import hashlib
import time
from typing import Optional

from ..storage.sqlite_storage import SQLiteStorage


class CatalogService:
    """Business logic for catalog items."""

    def __init__(self, storage: SQLiteStorage):
        self._storage = storage
        from ..config import get_config
        self._auto_approve = get_config().CATALOG_AUTO_APPROVE

    def create_item(
        self,
        item_type: str,
        title: str,
        description: Optional[str] = None,
        tags: Optional[str] = None,
        language: str = "ru",
        region: Optional[str] = None,
        join_mode: str = "open",
        invite_url: Optional[str] = None,
        author_contact: Optional[str] = None,
        admin_contact: Optional[str] = None,
        proposal_contact: Optional[str] = None,
        proposal_group_invite: Optional[str] = None,
        proposal_instruction: Optional[str] = None,
        source_ref: Optional[str] = None,
        avatar_file_path: Optional[str] = None,
        created_by_user_id: Optional[str] = None,
    ) -> dict:
        """Create a new catalog item."""
        now = _now()
        status = "active" if self._auto_approve else "pending"
        avatar_status = "ok" if avatar_file_path else "none"

        item = self._storage.execute_returning(
            """
            INSERT INTO catalog_items
                (type, title, description, tags, language, region,
                 join_mode, invite_url, author_contact, admin_contact,
                 proposal_contact, proposal_group_invite, proposal_instruction,
                 source_ref, avatar_file_path, avatar_status,
                 status, created_by_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id, type, title, status, created_at
            """,
            (
                item_type,
                title,
                description,
                tags,
                language,
                region,
                join_mode,
                invite_url,
                author_contact,
                admin_contact,
                proposal_contact,
                proposal_group_invite,
                proposal_instruction,
                source_ref,
                avatar_file_path,
                avatar_status,
                status,
                created_by_user_id,
                now,
                now,
            ),
        )
        # Update avatar_file_path with new id if we had one
        if avatar_file_path and "id" in item:
            self._storage.execute(
                "UPDATE catalog_items SET avatar_file_path = ? WHERE id = ?",
                (avatar_file_path, item["id"]),
            )
        return item

    def search(
        self,
        query: str,
        item_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Search active catalog items by text and optional type filter."""
        conditions = ["status = 'active'"]
        params: list = []

        if item_type:
            conditions.append("type = ?")
            params.append(item_type)

        if query:
            conditions.append(
                "(title LIKE ? OR description LIKE ? OR tags LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like])

        where = " AND ".join(conditions)

        count_row = self._storage.fetchone(
            f"SELECT COUNT(*) as cnt FROM catalog_items WHERE {where}", params
        )
        total = count_row["cnt"] if count_row else 0

        items = self._storage.fetchall(
            f"SELECT * FROM catalog_items WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )

        return {"items": items, "total": total}

    def get_item(self, item_id: int) -> Optional[dict]:
        """Get a single catalog item by ID (only if active, not deleted)."""
        return self._storage.fetchone(
            "SELECT * FROM catalog_items WHERE id = ? AND status != 'deleted_by_admin'",
            (item_id,),
        )

    def get_item_any_status(self, item_id: int) -> Optional[dict]:
        """Get a single catalog item by ID regardless of status."""
        return self._storage.fetchone(
            "SELECT * FROM catalog_items WHERE id = ?", (item_id,)
        )

    def list_new(self, limit: int = 10) -> list[dict]:
        """Get most recently created active items."""
        return self._storage.fetchall(
            "SELECT * FROM catalog_items WHERE status = 'active' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    def list_all_paginated(self, page: int = 1, page_size: int = 20) -> dict:
        """Get all active items grouped by type, paginated.

        Returns dict with:
            items: list of items for this page
            total: total active items
            page: current page
            pages: total pages
            groups: list of (type, count) tuples
        """
        total = self._storage.fetchone(
            "SELECT COUNT(*) as cnt FROM catalog_items WHERE status='active'",
        )["cnt"]

        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size

        items = self._storage.fetchall(
            "SELECT * FROM catalog_items WHERE status='active' ORDER BY type, title LIMIT ? OFFSET ?",
            (page_size, offset),
        )

        groups = self._storage.fetchall(
            "SELECT type, COUNT(*) as cnt FROM catalog_items WHERE status='active' GROUP BY type ORDER BY type",
        )

        return {
            "items": items,
            "total": total,
            "page": page,
            "pages": total_pages,
            "groups": groups,
        }

    def update_status(self, item_id: int, status: str) -> None:
        """Update item status (hide, show, etc.)."""
        self._storage.execute(
            "UPDATE catalog_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), item_id),
        )

    def get_user_items(self, user_id: str) -> list[dict]:
        """Get items created by a specific user."""
        return self._storage.fetchall(
            "SELECT * FROM catalog_items WHERE created_by_user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )

    def delete_item(self, item_id: int, user_id: str) -> bool:
        """Delete an item if owned by the user. Cleans up files and related data."""
        row = self._storage.fetchone(
            "SELECT id, avatar_file_path FROM catalog_items WHERE id = ? AND created_by_user_id = ?",
            (item_id, user_id),
        )
        if not row:
            return False
        self._hard_delete(row)
        return True

    def admin_delete_item(self, item_id: int) -> bool:
        """Delete ANY item by admin. No ownership check. Cleans up all related data."""
        row = self._storage.fetchone(
            "SELECT id, avatar_file_path FROM catalog_items WHERE id = ?", (item_id,)
        )
        if not row:
            return False
        self._hard_delete(row)
        return True

    def _hard_delete(self, row: dict) -> None:
        """Common hard-delete logic: removes avatar file, reports, sources, posts, and the item itself."""
        item_id = row["id"]
        # Clean up avatar file on disk
        avatar_path = row.get("avatar_file_path")
        if avatar_path:
            try:
                import os
                if os.path.isfile(avatar_path):
                    os.unlink(avatar_path)
            except Exception:
                pass
        # Clean up related reports
        self._storage.execute(
            "DELETE FROM reports WHERE catalog_item_id = ?", (item_id,)
        )
        # Clean up related telegram_sources and their posts
        sources = self._storage.fetchall(
            "SELECT id FROM telegram_sources WHERE catalog_item_id = ?", (item_id,)
        )
        for src in sources:
            self._storage.execute(
                "DELETE FROM telegram_posts WHERE source_id = ?", (src["id"],)
            )
        self._storage.execute(
            "DELETE FROM telegram_sources WHERE catalog_item_id = ?", (item_id,)
        )
        # Final delete of the item
        self._storage.execute(
            "DELETE FROM catalog_items WHERE id = ?", (item_id,)
        )

    def find_by_invite(self, invite_url: str) -> Optional[dict]:
        """Find catalog item by invite_url (exact match)."""
        return self._storage.fetchone(
            "SELECT id, title, admin_contact FROM catalog_items WHERE invite_url = ? AND status != 'deleted_by_admin'",
            (invite_url,),
        )

    def update_admin_contact(self, item_id: int, admin_contact: str) -> None:
        """Update admin_contact for a catalog item."""
        self._storage.execute(
            "UPDATE catalog_items SET admin_contact = ?, updated_at = ? WHERE id = ?",
            (admin_contact, _now(), item_id),
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
