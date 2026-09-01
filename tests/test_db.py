import sqlite3
from pathlib import Path

from tbr_shelf.db import MIGRATIONS, Database


def columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_fresh_database_reaches_latest_version(tmp_path: Path) -> None:
    db = Database(tmp_path / "library.db")
    assert db.migrate() == len(MIGRATIONS)
    assert db.schema_version() == len(MIGRATIONS)
    assert {"spine_pref", "physical_format", "store_url"} <= columns(db.path, "books")
    assert columns(db.path, "voice_turns")


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "library.db")
    db.migrate()
    db.migrate()
    assert db.schema_version() == len(MIGRATIONS)


def test_legacy_v1_file_is_upgraded_in_place(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        MIGRATIONS[0](connection)
        connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES(1)")
        connection.execute(
            "INSERT INTO books(title, author, shelf, tags, date_added, created_at, updated_at) "
            "VALUES('Dune', 'Frank Herbert', 'Audible', 'sci fi; SCI FI', '2026-01-01', 't', 't')"
        )
    db = Database(path)
    db.migrate()
    with sqlite3.connect(path) as connection:
        tags, spine_pref = connection.execute("SELECT tags, spine_pref FROM books").fetchone()
    assert tags == "Sci Fi"
    assert spine_pref == "auto"
    assert db.schema_version() == len(MIGRATIONS)


def test_spine_preference_rename_from_earlier_naming(tmp_path: Path) -> None:
    path = tmp_path / "rename.db"
    db = Database(path)
    db.migrate()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO books(title, shelf, date_added, created_at, updated_at, spine_pref) "
            "VALUES('X', 'Chirp', 'd', 't', 't', 'skill')"
        )
        connection.execute("UPDATE schema_version SET version=6")
    db.migrate()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT spine_pref FROM books").fetchone()[0] == "external"
