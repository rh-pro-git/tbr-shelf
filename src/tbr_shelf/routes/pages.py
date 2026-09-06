"""The one HTML page. Everything else is JSON or media."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..books import ListFilter, all_tags, get_book, list_books, shelf_counts
from ..config import SHELVES, STATUSES
from ..context import AppContext
from ..lookup import parse_lookup_envelope
from ..spines import SPINE_REV, effective_spine_source, spine_meta
from . import get_ctx

PACKAGE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATES_DIR = PACKAGE_DIR / "templates"

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SORT_LABELS = (
    ("added", "Newest first"),
    ("title", "Title A–Z"),  # noqa: RUF001
    ("author", "Author A–Z"),  # noqa: RUF001
    ("year", "Year, newest"),
    ("finished", "Recently finished"),
)


def asset_version() -> int:
    """Cache-buster for the static bundle: the newest mtime among the JS and CSS files."""
    return int(max(path.stat().st_mtime for path in STATIC_DIR.iterdir() if path.suffix in (".js", ".css")))


def decorate_for_view(ctx: AppContext, book: dict) -> dict:
    envelope = parse_lookup_envelope(book["lookup_candidates"])
    book["cands"] = envelope.get("candidates") or []
    book["cands_json"] = json.dumps(book["cands"])
    book["conflicts"] = envelope.get("conflicts") or []
    book["checked_at"] = envelope.get("checked_at") or ""
    book["src_errors"] = ", ".join(
        f"{name}: {status}" for name, status in (envelope.get("sources") or {}).items() if status != "ok"
    )
    meta = spine_meta(book)
    book["spine_src"] = effective_spine_source(ctx, book)
    book["spine_h_in"] = meta.get("height_in")
    book["spine_t_in"] = meta.get("display_thickness_in") or meta.get("thickness_in")
    book["spine_meta_d"] = meta
    if book["spine_src"] == "local":
        book["spine_kind"] = "local"
    else:
        book["spine_kind"] = "photo" if "photo" in (meta.get("source_type") or "").lower() else "generated"
    return book


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    shelf: str = "All",
    status: str = "",
    tag: str = "",
    q: str = "",
    archived: int = 0,
    sort: str = "added",
    ctx: AppContext = Depends(get_ctx),
) -> HTMLResponse:
    if shelf not in (*SHELVES, "All", "TBR"):
        shelf = "All"
    if shelf == "TBR":  # a virtual shelf: the TBR status across every real shelf
        status = "TBR"
    if sort not in dict(SORT_LABELS):
        sort = "added"
    filters = ListFilter(shelf=shelf, status=status, tag=tag, query=q, archived=bool(archived), sort=sort)
    books = [decorate_for_view(ctx, book) for book in list_books(ctx.db, filters)]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "books": books,
            "counts": shelf_counts(ctx.db),
            "shelves": SHELVES,
            "statuses": STATUSES,
            "tags": all_tags(ctx.db),
            "sort_labels": SORT_LABELS,
            "shelf": shelf,
            "status": status,
            "tag": tag,
            "q": q,
            "archived": int(archived),
            "sort": sort,
            "asset_v": asset_version(),
            "spine_rev": SPINE_REV,
            "features": ctx.settings.feature_flags(),
        },
    )


@router.get("/api/books/{book_id}/detail", response_class=HTMLResponse)
async def book_detail(request: Request, book_id: int, ctx: AppContext = Depends(get_ctx)) -> HTMLResponse:
    """A tile's detail block, rendered when the tile is first opened so the page itself stays light."""
    book = decorate_for_view(ctx, get_book(ctx.db, book_id))
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "b": book,
            "shelves": SHELVES,
            "statuses": STATUSES,
            "spine_rev": SPINE_REV,
            "features": ctx.settings.feature_flags(),
        },
        headers={"Cache-Control": "no-store"},
    )
