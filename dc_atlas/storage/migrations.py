"""
Database migration runner for DC Atlas.

Migrations are stored in SQL files under migrations/ directory.
Schema version is tracked in schema_version table.
"""

import logging
from pathlib import Path

from .sqlite_storage import SQLiteStorage

logger = logging.getLogger(__name__)

# Inline migrations for self-contained deployment
INIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dc_contact_id TEXT UNIQUE,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    tags TEXT,
    language TEXT NOT NULL DEFAULT 'ru',
    region TEXT,
    join_mode TEXT NOT NULL DEFAULT 'open',
    invite_url TEXT,
    author_contact TEXT,
    admin_contact TEXT,
    proposal_contact TEXT,
    proposal_group_invite TEXT,
    proposal_instruction TEXT,
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    trust_level INTEGER NOT NULL DEFAULT 0,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS telegram_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    original_url TEXT NOT NULL,
    title TEXT,
    description TEXT,
    catalog_item_id INTEGER,
    deltachat_channel_id TEXT,
    deltachat_invite_url TEXT,
    last_post_id INTEGER,
    last_checked_at TEXT,
    last_success_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    error_message TEXT,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(catalog_item_id) REFERENCES catalog_items(id),
    FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS telegram_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    telegram_post_id INTEGER NOT NULL,
    text_hash TEXT,
    original_url TEXT NOT NULL,
    published_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, telegram_post_id),
    FOREIGN KEY(source_id) REFERENCES telegram_sources(id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_item_id INTEGER NOT NULL,
    reporter_user_id INTEGER,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    FOREIGN KEY(catalog_item_id) REFERENCES catalog_items(id),
    FOREIGN KEY(reporter_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(actor_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_catalog_type ON catalog_items(type);
CREATE INDEX IF NOT EXISTS idx_catalog_status ON catalog_items(status);
CREATE INDEX IF NOT EXISTS idx_catalog_created_at ON catalog_items(created_at);
CREATE INDEX IF NOT EXISTS idx_telegram_status ON telegram_sources(status);
CREATE INDEX IF NOT EXISTS idx_telegram_last_checked ON telegram_sources(last_checked_at);
"""

# Migration 2: Add avatar columns to telegram_sources and catalog_items
MIGRATION_2 = """
ALTER TABLE telegram_sources ADD COLUMN avatar_url TEXT;
ALTER TABLE telegram_sources ADD COLUMN avatar_file_path TEXT;
ALTER TABLE telegram_sources ADD COLUMN avatar_hash TEXT;
ALTER TABLE telegram_sources ADD COLUMN avatar_checked_at TEXT;
ALTER TABLE telegram_sources ADD COLUMN avatar_updated_at TEXT;
ALTER TABLE telegram_sources ADD COLUMN avatar_status TEXT DEFAULT 'unknown';
ALTER TABLE catalog_items ADD COLUMN avatar_file_path TEXT;
ALTER TABLE catalog_items ADD COLUMN avatar_hash TEXT;
ALTER TABLE catalog_items ADD COLUMN avatar_status TEXT DEFAULT 'none';
"""

# Migration 3: Add error tracking for telegram sources and post storage fields
MIGRATION_3 = """
ALTER TABLE telegram_sources ADD COLUMN consecutive_errors INTEGER DEFAULT 0;
ALTER TABLE telegram_posts ADD COLUMN text TEXT;
ALTER TABLE telegram_posts ADD COLUMN publish_status TEXT DEFAULT 'published';
ALTER TABLE telegram_posts ADD COLUMN error_message TEXT;
ALTER TABLE telegram_posts ADD COLUMN has_photo INTEGER DEFAULT 0;
ALTER TABLE telegram_posts ADD COLUMN photo_count INTEGER DEFAULT 0;
ALTER TABLE telegram_posts ADD COLUMN has_video INTEGER DEFAULT 0;
ALTER TABLE telegram_posts ADD COLUMN video_count INTEGER DEFAULT 0;
ALTER TABLE telegram_posts ADD COLUMN has_file INTEGER DEFAULT 0;
"""

MIGRATIONS = {
    1: INIT_SCHEMA,
    2: MIGRATION_2,
    3: MIGRATION_3,
}


def run_migrations(storage: SQLiteStorage) -> None:
    """Apply pending migrations."""
    current = _get_current_version(storage)
    target = max(MIGRATIONS.keys())

    for version in range(current + 1, target + 1):
        sql = MIGRATIONS.get(version)
        if sql:
            logger.info("Applying migration %d...", version)
            storage.executescript(sql)
            _set_version(storage, version)
            logger.info("Migration %d applied.", version)

    logger.info("Database schema at version %d", target)


def _get_current_version(storage: SQLiteStorage) -> int:
    try:
        row = storage.fetchone(
            "SELECT COALESCE(MAX(version), 0) as v FROM schema_version"
        )
        return row["v"] if row else 0
    except Exception:
        return 0


def _set_version(storage: SQLiteStorage, version: int) -> None:
    storage.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
