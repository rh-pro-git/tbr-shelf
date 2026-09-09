"""Binary endpoints: cached covers, rendered spines, summary audio, and the external spine-asset API."""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from .. import catalogs, net, spines, summaries
from ..books import get_book, update_book
from ..context import AppContext
from ..covers import cached_media_type, clamp_fit_width, cover_path, fit_cover
from ..speech import synthesize
from ..text import asin_from_url
from . import get_ctx

router = APIRouter(prefix="/api")

WEEK_CACHE = {"Cache-Control": "public, max-age=604800"}
EXTERNAL_SPINE_MAX_BYTES = 3_000_000


@router.get("/books/{book_id}/cover")
async def cover(book_id: int, w: int = 0, ctx: AppContext = Depends(get_ctx)):
    """The cached cover: the bytes as fetched, or with `w` a WebP resized to at most that width."""
    path = cover_path(ctx, book_id)
    if path.exists():
        if w:
            fitted = await asyncio.to_thread(fit_cover, ctx, book_id, clamp_fit_width(w))
            return FileResponse(fitted, media_type="image/webp", headers=WEEK_CACHE)
        return FileResponse(path, media_type=cached_media_type(path), headers=WEEK_CACHE)
    book = get_book(ctx.db, book_id)
    return RedirectResponse(book["cover"] if book.get("cover") else "/static/cover.svg")


@router.get("/books/{book_id}/spine")
async def spine(book_id: int, w: int = 48, h: int = 190, src: str = "", ctx: AppContext = Depends(get_ctx)):
    width, height = spines.clamp_size(w, h)
    book = get_book(ctx.db, book_id)
    if src == "external" and not spines.external_spine_path(ctx, book_id).exists():
        raise HTTPException(404, "no external spine")
    if src == "external" or (src != "local" and spines.effective_spine_source(ctx, book) == "external"):
        fitted = await asyncio.to_thread(spines.fit_external_spine, ctx, book_id, width, height)
        return FileResponse(fitted, media_type="image/webp", headers=WEEK_CACHE)
    try:
        rendered = await spines.local_spine(ctx, book, width, height)
    except Exception as exc:
        raise HTTPException(404, "spine render failed") from exc
    if rendered is None:
        raise HTTPException(404, "no cached cover")
    return FileResponse(rendered, media_type="image/webp", headers=WEEK_CACHE)


@router.get("/spine-queue")
async def spine_queue(all: int = 0, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Work list for an external spine generator: queued books first, optionally everything unrendered."""
    condition = "(spine_state='queued' OR spine_meta IS NULL)" if all else "spine_state='queued'"
    with ctx.db.transaction() as connection:
        rows = connection.execute(
            "SELECT id, title, author, shelf, physical_format, page_count, audiobook_length, version, "
            f"spine_state, spine_meta FROM books WHERE archived=0 AND {condition} "
            "ORDER BY spine_state='queued' DESC, id"
        ).fetchall()
    return {"books": [dict(row) for row in rows]}


@router.put("/books/{book_id}/spine-asset")
async def put_spine_asset(
    book_id: int,
    meta: str = Form(...),
    file: UploadFile | None = File(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict:
    """An external generator delivers a PNG plus metadata: real height and thickness in inches,
    source type, confidence, edition notes."""
    book = get_book(ctx.db, book_id)
    try:
        metadata = json.loads(meta)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "meta must be a JSON object") from exc
    if not isinstance(metadata, dict):
        raise HTTPException(422, "meta must be a JSON object")
    for key in ("height_in", "thickness_in", "display_thickness_in"):
        if metadata.get(key) is not None and not isinstance(metadata[key], int | float):
            raise HTTPException(422, f"{key} must be numeric")
    changes = {
        "spine_meta": json.dumps(metadata),
        "spine_state": metadata.get("state") or ("ready" if file else "failed"),
    }
    if file is not None:
        data = await file.read()
        if not data.startswith(b"\x89PN") or len(data) > EXTERNAL_SPINE_MAX_BYTES:
            raise HTTPException(422, "file must be a PNG under 3MB")
        ctx.settings.external_spines_dir.mkdir(parents=True, exist_ok=True)
        spines.external_spine_path(ctx, book_id).write_bytes(data)
        for stale in ctx.settings.spines_dir.glob(f"{book_id}-external-*"):
            stale.unlink(missing_ok=True)
    updated = update_book(ctx.db, book_id, book["version"], changes)
    return {"book": updated, "effective": spines.effective_spine_source(ctx, updated)}


@router.get("/books/{book_id}/sample")
async def narration_sample(book_id: int, ctx: AppContext = Depends(get_ctx)) -> RedirectResponse:
    """Audible's narration sample for the edition the store link names, resolved on request.
    Nothing is stored."""
    asin = asin_from_url(get_book(ctx.db, book_id).get("store_url"))
    if not asin:
        raise HTTPException(404, "No Audible edition on this book")
    try:
        product = await catalogs.audible_product(asin)
    except (TimeoutError, httpx.HTTPError) as exc:
        raise HTTPException(503, f"Audible is not answering ({net.describe_error(exc)}).") from exc
    if not (product and product.get("sample_url")):
        raise HTTPException(404, "Audible has no sample for this edition")
    return RedirectResponse(product["sample_url"], status_code=302)


@router.get("/books/{book_id}/audio")
async def summary_audio(book_id: int, ctx: AppContext = Depends(get_ctx)):
    book = get_book(ctx.db, book_id)
    text = (book.get("summary") or "").strip()
    if not text:
        raise HTTPException(404, "No summary available to read yet.")
    path = summaries.audio_path(ctx, book_id)
    if not path.exists():
        audio = await synthesize(ctx, text)
        ctx.settings.audio_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    return FileResponse(path, media_type="audio/mpeg")
