"""The metadata lookup task and the rules for applying what it finds.

Lookup never overwrites what the reader typed. It fills blanks, and when the
evidence is ambiguous (no exact match, or an author conflict) it parks the book
in a `verify` state for a human to resolve instead of guessing.

Refresh is the one explicit exception: the same pipeline with `refresh=True`
replaces the source-derived fields with what the catalogs say now. It still never
blanks a field, never touches the author rule, and never swaps the chosen cover.
"""

from __future__ import annotations

import json
import logging

from . import catalogs
from .books import get_book, update_book, update_book_latest
from .config import PRINT_FORMATS
from .context import AppContext
from .covers import cache_cover, drop_cover_cache
from .text import audible_product_url, is_asin, match_key

log = logging.getLogger(__name__)

FILLABLE_FROM_MATCH = ("author", "year", "page_count", "physical_format")
REFRESHABLE = ("year", "page_count", "physical_format")  # author stays blank-fill even on refresh


def parse_lookup_envelope(raw: str | None) -> dict:
    try:
        envelope = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"candidates": []}
    if isinstance(envelope, list):
        return {"candidates": envelope}
    return envelope if isinstance(envelope, dict) else {"candidates": []}


def author_conflict(book: dict, best: dict | None) -> list[dict]:
    if not (best and book.get("author") and best.get("author")):
        return []
    if catalogs.authors_overlap(book["author"], best["author"]):
        return []
    return [{"field": "author", "current": book["author"], "candidate": best["author"]}]


def lookup_changes(
    book: dict, candidates: list[dict], sources: dict[str, str], *, refresh: bool = False
) -> dict:
    """Pure decision: given what the catalogs returned, what should change on the book.

    With `refresh`, fields the catalogs own are replaced when the match has a value for them."""
    best = catalogs.match_exact(book, candidates)
    conflicts = author_conflict(book, best)
    changes: dict = {
        "lookup_candidates": json.dumps(
            {"candidates": candidates, "sources": sources, "conflicts": conflicts, "checked_at": _stamp()}
        )
    }
    core_failed = any(sources.get(name) != "ok" for name in catalogs.VERDICT_SOURCES)
    if not candidates:
        changes["lookup_state"] = "failed" if core_failed else "not_found"
    elif best is None or conflicts:
        changes["lookup_state"] = "verify"
    else:
        changes["lookup_state"] = "ready"
        for field in FILLABLE_FROM_MATCH:
            replace = refresh and field in REFRESHABLE
            if best.get(field) and (replace or not book.get(field)):
                changes[field] = best[field]

    audible = catalogs.match_exact(book, [c for c in candidates if c.get("asin")])
    if audible:
        if book["shelf"] in ("Audible", "Wishlist") and (refresh or not book.get("store_url")):
            changes["store_url"] = audible_product_url(audible["asin"])
        if audible.get("minutes") and (refresh or not book.get("audiobook_length")):
            changes["audiobook_length"] = catalogs.runtime_text(audible)
        if (
            changes["lookup_state"] == "ready"
            and "year" not in changes
            and (refresh or not book.get("year"))
            and audible.get("year")
        ):
            changes["year"] = audible["year"]

    # Covers fill only when blank; replacing one is always the reader's choice. The default is
    # the author-verified Audible edition's art (what the listening apps show), else the best match's.
    if changes["lookup_state"] == "ready" and not book.get("cover") and best is not None:
        cover = (audible or {}).get("audible_cover") or (audible or {}).get("cover")
        cover = cover or best.get("audible_cover") or best.get("cover")
        if cover:
            changes["cover"] = cover
    return changes


async def run_lookup(ctx: AppContext, book_id: int, refresh: bool = False) -> None:
    book = get_book(ctx.db, book_id)
    try:
        candidates, sources = await catalogs.search_candidates(book["title"], book["author"])
        changes = lookup_changes(book, candidates, sources, refresh=refresh)
        update_book(ctx.db, book_id, book["version"], changes)
        if "cover" in changes:
            drop_cover_cache(ctx, book_id)
            await cache_cover(ctx, book_id)
        elif refresh:
            await cache_cover(
                ctx, book_id
            )  # same URL: fetched first, so a failed download keeps the old copy
    except Exception as exc:
        log.warning("lookup failed for book %s: %s", book_id, exc)
        update_book_latest(ctx.db, book_id, {"lookup_state": "failed"})


def candidate_changes(book: dict, candidate: dict) -> tuple[dict, bool]:
    """Changes to apply when the reader accepts a candidate, and whether the summary must be redone."""
    changes: dict = {"lookup_state": "accepted"}
    for field in ("title", "author", "cover"):
        value = candidate.get(field)
        if isinstance(value, str) and value.strip():
            changes[field] = value.strip()[:500]
    for field in ("year", "page_count"):
        if isinstance(candidate.get(field), int):
            changes[field] = candidate[field]
    if candidate.get("physical_format") in PRINT_FORMATS:
        changes["physical_format"] = candidate["physical_format"]
    asin = candidate.get("asin")
    if isinstance(asin, str) and is_asin(asin):
        if book["shelf"] in ("Audible", "Wishlist") and not book.get("store_url"):
            changes["store_url"] = audible_product_url(asin)
        if isinstance(candidate.get("minutes"), int) and not book.get("audiobook_length"):
            changes["audiobook_length"] = catalogs.runtime_text(candidate)
    identity_changed = ("title" in changes and match_key(changes["title"]) != match_key(book["title"])) or (
        "author" in changes and match_key(changes["author"]) != match_key(book["author"])
    )
    return changes, identity_changed


def _stamp() -> str:
    from .db import utc_now

    return utc_now()
