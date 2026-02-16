"""
SQLiteMemoryStore — SQLite-backed persistent MemoryStore.

Zero external dependencies (uses Python stdlib ``sqlite3``).
Suitable for local production deployments.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime
from typing import List, Optional


_INIT_SQL = """
CREATE TABLE IF NOT EXISTS memory_kv (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (namespace, key)
);

CREATE TABLE IF NOT EXISTS memory_list (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_list_ns_key
    ON memory_list(namespace, key);
"""


class SQLiteMemoryStore:
    """SQLite-backed MemoryStore.

    Parameters:
        db_path: Path to the SQLite database file (e.g. ``"memory.db"``).
            Use ``":memory:"`` for an in-process database (testing).
    """

    def __init__(self, db_path: str = "memory.db") -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._initialized = False

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.row_factory = sqlite3.Row
        if not self._initialized:
            self._conn.executescript(_INIT_SQL)
            self._initialized = True
        return self._conn

    def _run_sync(self, fn):
        with self._lock:
            return fn(self._get_conn())

    async def _run(self, fn):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._run_sync(fn))

    # ── KV ──

    async def get(self, namespace: str, key: str) -> Optional[str]:
        def _do(conn):
            row = conn.execute(
                "SELECT value FROM memory_kv WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            return row["value"] if row else None
        return await self._run(_do)

    async def set(self, namespace: str, key: str, value: str) -> None:
        def _do(conn):
            conn.execute(
                """INSERT INTO memory_kv (namespace, key, value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(namespace, key) DO UPDATE SET value=?, updated_at=?""",
                (namespace, key, value, _now(), value, _now()),
            )
            conn.commit()
        await self._run(_do)

    async def delete(self, namespace: str, key: str) -> None:
        def _do(conn):
            conn.execute(
                "DELETE FROM memory_kv WHERE namespace=? AND key=?",
                (namespace, key),
            )
            conn.commit()
        await self._run(_do)

    async def list_keys(self, namespace: str) -> List[str]:
        def _do(conn):
            rows = conn.execute(
                """SELECT DISTINCT key FROM (
                       SELECT key FROM memory_kv WHERE namespace=?
                       UNION
                       SELECT DISTINCT key FROM memory_list WHERE namespace=?
                   )""",
                (namespace, namespace),
            ).fetchall()
            return [r["key"] for r in rows]
        return await self._run(_do)

    # ── List ──

    async def append(self, namespace: str, key: str, value: str) -> None:
        def _do(conn):
            conn.execute(
                "INSERT INTO memory_list (namespace, key, value) VALUES (?, ?, ?)",
                (namespace, key, value),
            )
            conn.commit()
        await self._run(_do)

    async def get_list(
        self, namespace: str, key: str, limit: int = 0, offset: int = 0
    ) -> List[str]:
        def _do(conn):
            sql = "SELECT value FROM memory_list WHERE namespace=? AND key=? ORDER BY id ASC"
            params: list = [namespace, key]
            if limit > 0:
                sql += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            elif offset > 0:
                sql += " LIMIT -1 OFFSET ?"
                params.append(offset)
            return [r["value"] for r in conn.execute(sql, params).fetchall()]
        return await self._run(_do)

    async def trim_list(self, namespace: str, key: str, max_size: int) -> None:
        def _do(conn):
            conn.execute(
                """DELETE FROM memory_list WHERE id IN (
                       SELECT id FROM memory_list
                       WHERE namespace=? AND key=?
                       ORDER BY id ASC
                       LIMIT MAX(0, (SELECT COUNT(*) FROM memory_list
                                     WHERE namespace=? AND key=?) - ?)
                   )""",
                (namespace, key, namespace, key, max_size),
            )
            conn.commit()
        await self._run(_do)

    async def clear_list(self, namespace: str, key: str) -> None:
        def _do(conn):
            conn.execute(
                "DELETE FROM memory_list WHERE namespace=? AND key=?",
                (namespace, key),
            )
            conn.commit()
        await self._run(_do)

    async def list_length(self, namespace: str, key: str) -> int:
        def _do(conn):
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_list WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            return row["cnt"]
        return await self._run(_do)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


def _now() -> str:
    return datetime.now().isoformat()
