"""Bulk entry points: a loose CSV that gets enriched afterwards, and pre-enriched rows that do not."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date

from .books import dedupe_key, existing_dedupe_keys, insert_book
from .config import SHELVES, STATUSES
from .context import AppContext
from .models import ImportedBook
from .text import asin_from_url, audible_product_url, normalize_tags

CSV_MAX_BYTES = 5_000_000
CSV_FIELDS = (
    "title",
    "author",
    "series",
    "shelf",
    "status",
    "tags",
    "notes",
    "year",
    "page_count",
    "audiobook_length",
    "date_added",
    "started_at",
    "finished_at",
    "store_url",
)


@dataclass
class ImportResult:
    added_ids: list[int] = field(default_factory=list)
    skipped_duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        text = f"Imported {len(self.added_ids)} book(s), {self.skipped_duplicates} duplicate(s) skipped."
        if self.errors:
            text += f" {len(self.errors)} row error(s)."
        return text


class CsvFormatError(ValueError):
    pass


def _shelf_of(raw: str) -> str:
    return next((shelf for shelf in SHELVES if shelf.lower() == raw.lower()), "Wishlist")


def _status_of(raw: str) -> str:
    return raw if raw in STATUSES else "Unread"


def _int_or_none(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """Rows keyed by canonical field name (headers are matched case-insensitively), plus row errors."""
    reader = csv.DictReader(io.StringIO(text))
    headers = {(name or "").strip().lower(): name for name in reader.fieldnames or []}
    if "title" not in headers:
        raise CsvFormatError("CSV must have a 'title' column")
    rows: list[dict] = []
    errors: list[str] = []
    for line_number, raw in enumerate(reader, start=2):
        row = {key: (raw.get(headers[key]) or "").strip() if key in headers else "" for key in CSV_FIELDS}
        if not row["title"]:
            if any((value or "").strip() for value in raw.values()):
                errors.append(f"Row {line_number}: missing title, skipped")
            continue
        rows.append(row)
    return rows, errors


def import_csv_rows(ctx: AppContext, rows: list[dict]) -> ImportResult:
    """Insert loose rows. They are queued for lookup (and summary when a model exists) by the caller."""
    result = ImportResult()
    summary_state = "queued" if ctx.settings.has_llm else "none"
    with ctx.db.transaction() as connection:
        seen = existing_dedupe_keys(connection)
        for row in rows:
            shelf = _shelf_of(row["shelf"] or "Wishlist")
            key = dedupe_key(row["title"], row["author"], shelf)
            if key in seen:
                result.skipped_duplicates += 1
                continue
            seen.add(key)
            book_id = insert_book(
                connection,
                {
                    "title": row["title"][:500],
                    "author": row["author"][:500],
                    "series": row["series"] or None,
                    "shelf": shelf,
                    "status": _status_of(row["status"] or "Unread"),
                    "tags": normalize_tags(row["tags"]),
                    "notes": row["notes"],
                    "year": _int_or_none(row["year"]),
                    "page_count": _int_or_none(row["page_count"]),
                    "audiobook_length": row["audiobook_length"],
                    "date_added": row["date_added"] or date.today().isoformat(),
                    "started_at": row["started_at"] or None,
                    "finished_at": row["finished_at"] or None,
                    "store_url": row["store_url"] or None,
                    "lookup_state": "queued",
                    "summary_state": summary_state,
                },
            )
            result.added_ids.append(book_id)
    return result


def import_enriched(ctx: AppContext, books: list[ImportedBook]) -> ImportResult:
    """Insert rows that already carry verified metadata. Only the cover fetch is queued afterwards.

    Duplicates are detected by ASIN when one is present, else by title, author and shelf.
    """
    result = ImportResult()
    today = date.today().isoformat()
    with ctx.db.transaction() as connection:
        seen_keys = existing_dedupe_keys(connection)
        seen_asins = {
            asin
            for (store_url,) in connection.execute("SELECT store_url FROM books").fetchall()
            if (asin := asin_from_url(store_url))
        }
        for book in books:
            key = dedupe_key(book.title, book.author, book.shelf)
            if key in seen_keys or (book.asin and book.asin in seen_asins):
                result.skipped_duplicates += 1
                continue
            seen_keys.add(key)
            if book.asin:
                seen_asins.add(book.asin)
            summary = book.summary.strip()
            book_id = insert_book(
                connection,
                {
                    "title": book.title.strip()[:500],
                    "author": book.author.strip()[:500],
                    "narrator": (book.narrator or "").strip() or None,
                    "series": (book.series or "").strip() or None,
                    "shelf": book.shelf,
                    "status": _status_of(book.status),
                    "tags": normalize_tags(book.tags),
                    "notes": book.notes,
                    "year": book.year,
                    "page_count": book.page_count,
                    "physical_format": book.physical_format,
                    "audiobook_length": book.audiobook_length.strip(),
                    "cover": book.cover.strip(),
                    "summary": summary,
                    "store_url": book.store_url or (audible_product_url(book.asin) if book.asin else None),
                    "date_added": book.date_added or today,
                    "started_at": book.started_at,
                    "finished_at": book.finished_at,
                    "lookup_state": "ready",
                    "summary_state": "ready" if summary else "none",
                },
            )
            result.added_ids.append(book_id)
    return result
