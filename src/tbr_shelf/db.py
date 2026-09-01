"""SQLite access and forward-only schema migrations.

The schema is versioned in a one-row `schema_version` table. Every migration is a
function applied in order inside one transaction; a fresh database runs them all.
Migrations only ever add columns or tables, so an older build can still open a
newer file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .text import normalize_tags

Migration = Callable[[sqlite3.Connection], None]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> int:
        with self.transaction() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
            if not connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]:
                connection.execute("INSERT INTO schema_version VALUES(0)")
            current = connection.execute("SELECT version FROM schema_version").fetchone()[0]
            for version, migration in enumerate(MIGRATIONS, start=1):
                if version > current:
                    migration(connection)
                    connection.execute("UPDATE schema_version SET version=?", (version,))
            return len(MIGRATIONS)

    def schema_version(self) -> int:
        with self.transaction() as connection:
            return connection.execute("SELECT version FROM schema_version").fetchone()[0]


def _create_books(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS books(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            series TEXT,
            shelf TEXT NOT NULL CHECK(shelf IN ('Audible','Chirp','Wishlist')),
            status TEXT NOT NULL DEFAULT 'Unread',
            tags TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            cover TEXT,
            year INTEGER,
            page_count INTEGER,
            audiobook_length TEXT,
            date_added TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            summary TEXT,
            summary_state TEXT NOT NULL DEFAULT 'none',
            lookup_candidates TEXT NOT NULL DEFAULT '[]',
            lookup_state TEXT NOT NULL DEFAULT 'none',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_books_shelf_archived ON books(shelf, archived)")


def _canonicalize_tags(connection: sqlite3.Connection) -> None:
    for book_id, tags in connection.execute("SELECT id, tags FROM books").fetchall():
        canonical = normalize_tags(tags)
        if canonical != tags:
            connection.execute("UPDATE books SET tags=? WHERE id=?", (canonical, book_id))


def _add_lifecycle_columns(connection: sqlite3.Connection) -> None:
    for column in ("started_at TEXT", "finished_at TEXT", "store_url TEXT"):
        connection.execute(f"ALTER TABLE books ADD COLUMN {column}")


def _create_voice_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_conversations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sid TEXT NOT NULL,
            started_at TEXT NOT NULL,
            last_at TEXT NOT NULL,
            closed INTEGER NOT NULL DEFAULT 0,
            ingested INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_vc_sid ON voice_conversations(sid, closed)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_turns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id INTEGER NOT NULL REFERENCES voice_conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _add_physical_format(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE books ADD COLUMN physical_format TEXT")


def _add_spine_columns(connection: sqlite3.Connection) -> None:
    for column in (
        "spine_meta TEXT",
        "spine_pref TEXT NOT NULL DEFAULT 'auto'",
        "spine_state TEXT NOT NULL DEFAULT 'none'",
    ):
        connection.execute(f"ALTER TABLE books ADD COLUMN {column}")


def _rename_spine_preference(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE books SET spine_pref='external' WHERE spine_pref='skill'")


MIGRATIONS: tuple[Migration, ...] = (
    _create_books,
    _canonicalize_tags,
    _add_lifecycle_columns,
    _create_voice_tables,
    _add_physical_format,
    _add_spine_columns,
    _rename_spine_preference,
)
