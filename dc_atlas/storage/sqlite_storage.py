"""
SQLite storage layer for DC Atlas.

All database access goes through this module.
No raw SQL outside of storage module and migrations.
"""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Optional


class SQLiteStorage:
    """Thread-safe SQLite wrapper with connection management."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._db_path

    def connect(self) -> None:
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def executescript(self, sql: str) -> None:
        """Execute multiple SQL statements (for migrations)."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        with self._lock:
            try:
                self._conn.executescript(sql)
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @contextmanager
    def cursor(self):
        if not self._conn:
            raise RuntimeError("Database not connected")
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @contextmanager
    def transaction(self):
        if not self._conn:
            raise RuntimeError("Database not connected")
        with self._lock:
            try:
                yield self._conn.cursor()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.cursor() as cur:
            return cur.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        with self.cursor() as cur:
            row = cur.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def execute_returning(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Execute INSERT/UPDATE and return the row via RETURNING clause."""
        with self.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


# Global singleton
_instance: Optional[SQLiteStorage] = None


def get_storage(db_path: str) -> SQLiteStorage:
    global _instance
    if _instance is None:
        _instance = SQLiteStorage(db_path)
        _instance.connect()
    return _instance
