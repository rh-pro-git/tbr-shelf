"""Book repository: reads, optimistic-locked writes, listing and dedupe keys."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .config import SHELVES
from .db import Database, utc_now
from .text import match_key, split_tags

SORT_ORDERS: dict[str, str] = {
    "added": "date_added DESC, id DESC",
    "title": "title COLLATE NOCASE ASC",
    "author": "author='', author COLLATE NOCASE ASC, title COLLATE NOCASE ASC",
    "year": "year IS NULL, year DESC",
    "finished": "finished_at IS NULL, finished_at DESC",
}


class BookNotFound(LookupError):
    pass


class VersionConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__("This book changed elsewhere. Reload before saving.")
        self.current_version = current_version


@dataclass(frozen=True)
class ListFilter:
    shelf: str = "All"
    status: str = ""
    tag: str = ""
    query: str = ""
    archived: bool = False
    sort: str = "added"


def get_book(db: Database, book_id: int) -> dict:
    with db.transaction() as connection:
        row = connection.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if row is None:
        raise BookNotFound(book_id)
    return dict(row)


def update_book(db: Database, book_id: int, expected_version: int, changes: dict) -> dict:
    """Apply `changes` only if the row is still at `expected_version`; bump the version on success."""
    if not changes:
        return get_book(db, book_id)
    fields = {**changes, "updated_at": utc_now(), "version": expected_version + 1}
    assignments = ", ".join(f"{column}=?" for column in fields)
    with db.transaction() as connection:
        cursor = connection.execute(
            f"UPDATE books SET {assignments} WHERE id=? AND version=?",
            (*fields.values(), book_id, expected_version),
        )
        if cursor.rowcount == 0:
            row = connection.execute("SELECT version FROM books WHERE id=?", (book_id,)).fetchone()
            if row is None:
                raise BookNotFound(book_id)
            raise VersionConflict(row[0])
        return dict(connection.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())


def update_book_latest(db: Database, book_id: int, changes: dict) -> dict:
    """Update against whatever version is current. For background tasks that own their state fields."""
    return update_book(db, book_id, get_book(db, book_id)["version"], changes)


def insert_book(connection: sqlite3.Connection, fields: dict) -> int:
    stamp = utc_now()
    row = {"created_at": stamp, "updated_at": stamp, **fields}
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    cursor = connection.execute(f"INSERT INTO books({columns}) VALUES({placeholders})", tuple(row.values()))
    return int(cursor.lastrowid)


def delete_archived_book(db: Database, book_id: int, expected_version: int) -> bool:
    with db.transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM books WHERE id=? AND version=? AND archived=1", (book_id, expected_version)
        )
        return cursor.rowcount > 0


def list_books(db: Database, filters: ListFilter) -> list[dict]:
    clauses = ["archived=?"]
    params: list[object] = [int(filters.archived)]
    if filters.shelf in SHELVES:
        clauses.append("shelf=?")
        params.append(filters.shelf)
    if filters.status:
        clauses.append("status=?")
        params.append(filters.status)
    if filters.query:
        clauses.append("(title LIKE ? OR author LIKE ? OR series LIKE ? OR tags LIKE ?)")
        params.extend([f"%{filters.query}%"] * 4)
    order = SORT_ORDERS.get(filters.sort, SORT_ORDERS["added"])
    with db.transaction() as connection:
        rows = connection.execute(
            f"SELECT * FROM books WHERE {' AND '.join(clauses)} ORDER BY {order}", params
        ).fetchall()
    books = [dict(row) for row in rows]
    if filters.tag:
        wanted = filters.tag.casefold()
        books = [b for b in books if wanted in {t.casefold() for t in split_tags(b["tags"])}]
    return books


def shelf_counts(db: Database) -> dict[str, int]:
    """Per-shelf counts plus the virtual TBR shelf: the TBR status across every real shelf."""
    with db.transaction() as connection:
        rows = connection.execute(
            "SELECT shelf, COUNT(*) FROM books WHERE archived=0 GROUP BY shelf"
        ).fetchall()
        tbr = connection.execute("SELECT COUNT(*) FROM books WHERE archived=0 AND status='TBR'").fetchone()[0]
    return {**{shelf: count for shelf, count in rows}, "TBR": tbr}


def all_tags(db: Database) -> list[str]:
    with db.transaction() as connection:
        rows = connection.execute("SELECT tags FROM books WHERE archived=0").fetchall()
    return sorted({tag for (tags,) in rows for tag in split_tags(tags)}, key=str.casefold)


def dedupe_key(title: str, author: str, shelf: str) -> tuple[str, str, str]:
    return (match_key(title), match_key(author), shelf)


def existing_dedupe_keys(connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
    rows = connection.execute("SELECT title, author, shelf FROM books").fetchall()
    return {dedupe_key(title, author, shelf) for title, author, shelf in rows}


def library_rows_for_prompt(db: Database) -> list[dict]:
    with db.transaction() as connection:
        rows = connection.execute(
            "SELECT title, author, series, shelf, status, tags, year, started_at, finished_at, notes "
            "FROM books WHERE archived=0 ORDER BY shelf, title"
        ).fetchall()
    return [dict(row) for row in rows]
