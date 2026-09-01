"""Book CRUD and the per-book actions: lookup, candidate acceptance, covers, summaries, suggestions."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import catalogs, summaries
from ..books import delete_archived_book, get_book, insert_book, update_book
from ..config import PRINT_FORMATS, STATUSES
from ..context import AppContext
from ..covers import cache_cover, drop_cover_cache
from ..lookup import candidate_changes, parse_lookup_envelope, run_lookup
from ..models import BookCreate, BookUpdate, CandidateAccept, VersionAction
from ..text import is_iso_date, match_key, normalize_tags
from . import get_ctx

router = APIRouter(prefix="/api/books")

MAX_ALT_COVERS = 24


def queue_enrichment(ctx: AppContext, bg: BackgroundTasks, book_id: int) -> None:
    bg.add_task(run_lookup, ctx, book_id)
    if ctx.settings.has_llm:
        bg.add_task(summaries.run_summary, ctx, book_id)


@router.post("")
async def create(
    payload: BookCreate, bg: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> JSONResponse:
    if payload.status not in STATUSES:
        raise HTTPException(422, "Invalid status")
    with ctx.db.transaction() as connection:
        book_id = insert_book(
            connection,
            {
                "title": payload.title.strip(),
                "author": payload.author.strip(),
                "series": payload.series,
                "shelf": payload.shelf,
                "status": payload.status,
                "tags": normalize_tags(payload.tags),
                "notes": payload.notes,
                "date_added": date.today().isoformat(),
                "lookup_state": "queued",
                "summary_state": "queued" if ctx.settings.has_llm else "none",
            },
        )
    queue_enrichment(ctx, bg, book_id)
    message = (
        "Added. Metadata"
        + (" and spoiler-free summary are" if ctx.settings.has_llm else " is")
        + " being prepared."
    )
    return JSONResponse({"book": get_book(ctx.db, book_id), "message": message}, status_code=201)


@router.get("/{book_id}")
async def read(book_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"book": get_book(ctx.db, book_id)}


def validated_changes(book: dict, payload: BookUpdate) -> dict:
    changes = payload.model_dump(exclude={"version"}, exclude_unset=True)
    if changes.get("status") not in (None, *STATUSES, "Archived"):
        raise HTTPException(422, "Invalid status")
    if "tags" in changes:
        changes["tags"] = normalize_tags(changes["tags"])
    for field in ("started_at", "finished_at"):
        if field in changes:
            value = (changes[field] or "").strip()
            if value and not is_iso_date(value):
                raise HTTPException(422, f"{field} must be YYYY-MM-DD")
            changes[field] = value or None
    if "physical_format" in changes:
        value = (changes["physical_format"] or "").strip()
        if value and value not in PRINT_FORMATS:
            raise HTTPException(422, "Invalid format")
        changes["physical_format"] = value or None
    if "store_url" in changes:
        value = (changes["store_url"] or "").strip()
        if value and not value.startswith("https://"):
            raise HTTPException(422, "store_url must be https")
        changes["store_url"] = value or None
    # Moving into Reading or Finished stamps the date once, unless the reader supplied one.
    today = date.today().isoformat()
    status = changes.get("status")
    if (
        status == "Reading"
        and book["status"] != "Reading"
        and not book.get("started_at")
        and not changes.get("started_at")
    ):
        changes["started_at"] = today
    if (
        status == "Finished"
        and book["status"] != "Finished"
        and not book.get("finished_at")
        and not changes.get("finished_at")
    ):
        changes["finished_at"] = today
    return changes


@router.patch("/{book_id}")
async def patch(
    book_id: int, payload: BookUpdate, bg: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> dict:
    book = get_book(ctx.db, book_id)
    changes = validated_changes(book, payload)
    updated = update_book(ctx.db, book_id, payload.version, changes)
    if "cover" in changes:
        drop_cover_cache(ctx, book_id)
        bg.add_task(cache_cover, ctx, book_id)
    return {"book": updated}


@router.post("/{book_id}/archive")
async def archive(book_id: int, payload: VersionAction, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"book": update_book(ctx.db, book_id, payload.version, {"archived": 1, "status": "Archived"})}


@router.post("/{book_id}/restore")
async def restore(book_id: int, payload: VersionAction, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"book": update_book(ctx.db, book_id, payload.version, {"archived": 0, "status": "Unread"})}


@router.delete("/{book_id}")
async def delete(book_id: int, payload: VersionAction, ctx: AppContext = Depends(get_ctx)) -> dict:
    if not delete_archived_book(ctx.db, book_id, payload.version):
        raise HTTPException(409, "Permanent deletion requires an archived, current book.")
    drop_cover_cache(ctx, book_id)
    summaries.drop_audio(ctx, book_id)
    return {"deleted": True}


@router.post("/{book_id}/lookup")
async def lookup(
    book_id: int, payload: VersionAction, bg: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> dict:
    update_book(ctx.db, book_id, payload.version, {"lookup_state": "queued"})
    bg.add_task(run_lookup, ctx, book_id)
    return {"message": "Lookup queued"}


@router.post("/{book_id}/accept-candidate")
async def accept_candidate(
    book_id: int, payload: CandidateAccept, bg: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> dict:
    book = get_book(ctx.db, book_id)
    changes, identity_changed = candidate_changes(book, payload.candidate)
    if identity_changed and ctx.settings.has_llm:
        changes.update(summary="", summary_state="queued")
    updated = update_book(ctx.db, book_id, payload.version, changes)
    if identity_changed and ctx.settings.has_llm:
        summaries.drop_audio(ctx, book_id)
        bg.add_task(summaries.run_summary, ctx, book_id)
    if "cover" in changes:
        drop_cover_cache(ctx, book_id)
        bg.add_task(cache_cover, ctx, book_id)
    return {"book": updated}


@router.post("/{book_id}/confirm-lookup")
async def confirm_lookup(book_id: int, payload: VersionAction, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"book": update_book(ctx.db, book_id, payload.version, {"lookup_state": "accepted"})}


@router.get("/{book_id}/altcovers")
async def alternative_covers(book_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every cover for this title: the exact-title candidates' art, then Open Library's editions."""
    book = get_book(ctx.db, book_id)
    covers: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if url and url not in seen:
            seen.add(url)
            covers.append(url)

    for candidate in parse_lookup_envelope(book["lookup_candidates"]).get("candidates", []):
        if match_key(candidate.get("title", "")) == match_key(book["title"]):
            add(candidate.get("audible_cover"))
            add(candidate.get("cover"))
    try:
        for url in await catalogs.open_library_edition_covers(book["title"], book["author"] or ""):
            add(url)
    except Exception:
        pass
    return {"covers": covers[:MAX_ALT_COVERS], "current": book.get("cover") or ""}


@router.post("/{book_id}/summary")
async def regenerate_summary(
    book_id: int, payload: VersionAction, bg: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> dict:
    if not ctx.settings.has_llm:
        raise HTTPException(503, "No language model is configured.")
    update_book(ctx.db, book_id, payload.version, {"summary": "", "summary_state": "queued"})
    summaries.drop_audio(ctx, book_id)
    bg.add_task(summaries.run_summary, ctx, book_id)
    return {"message": "Summary queued"}


@router.post("/{book_id}/similar")
async def similar(book_id: int, payload: VersionAction, ctx: AppContext = Depends(get_ctx)) -> dict:
    book = get_book(ctx.db, book_id)
    if book["version"] != payload.version:
        raise HTTPException(409, "This book changed elsewhere. Reload before requesting suggestions.")
    try:
        return {"suggestions": await summaries.suggest_similar(ctx, book)}
    except Exception as exc:  # model down, malformed JSON: both mean "not right now"
        raise HTTPException(503, "Suggestions are temporarily unavailable. Try again.") from exc


@router.post("/{book_id}/spine-regen")
async def request_external_spine(
    book_id: int, payload: VersionAction, ctx: AppContext = Depends(get_ctx)
) -> dict:
    return {"book": update_book(ctx.db, book_id, payload.version, {"spine_state": "queued"})}
