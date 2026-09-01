"""Bulk imports."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from ..context import AppContext
from ..covers import cache_cover
from ..importers import CSV_MAX_BYTES, CsvFormatError, import_csv_rows, import_enriched, parse_csv
from ..models import ImportedBook
from . import get_ctx
from .books import queue_enrichment

router = APIRouter(prefix="/api/import")

ENRICHED_IMPORT_MAX = 1000


@router.post("/csv")
async def import_csv(
    bg: BackgroundTasks, file: UploadFile = File(...), ctx: AppContext = Depends(get_ctx)
) -> dict:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(422, "File must be a .csv")
    raw = await file.read()
    if len(raw) > CSV_MAX_BYTES:
        raise HTTPException(413, "File too large (max 5MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "File must be UTF-8 text") from exc
    try:
        rows, errors = parse_csv(text)
    except CsvFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    result = import_csv_rows(ctx, rows)
    result.errors.extend(errors)
    for book_id in result.added_ids:
        queue_enrichment(ctx, bg, book_id)
    return {
        "added": len(result.added_ids),
        "skipped_duplicates": result.skipped_duplicates,
        "errors": result.errors[:20],
        "message": result.message,
    }


@router.post("/books")
async def import_books(
    books: list[ImportedBook], bg: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> dict:
    """Pre-enriched rows (for example, an Audible library resolved by ASIN). No lookup or summary is
    queued, only the cover fetch, so a 700-book import does not fan out into 1,400 background jobs."""
    if not books:
        raise HTTPException(422, "No books supplied")
    if len(books) > ENRICHED_IMPORT_MAX:
        raise HTTPException(413, f"Max {ENRICHED_IMPORT_MAX} books per request")
    result = import_enriched(ctx, books)
    for book_id in result.added_ids:
        bg.add_task(cache_cover, ctx, book_id)
    return {
        "added": len(result.added_ids),
        "skipped_duplicates": result.skipped_duplicates,
        "ids": result.added_ids,
    }
