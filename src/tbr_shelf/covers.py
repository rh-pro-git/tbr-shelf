"""Local cover cache. Covers are fetched once and served same-origin so the shelf can sample them."""

from __future__ import annotations

import logging
from pathlib import Path

from . import net
from .books import get_book
from .context import AppContext

log = logging.getLogger(__name__)

IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8", "image/jpeg"),
    (b"\x89PN", "image/png"),
    (b"RIFF", "image/webp"),
    (b"GIF8", "image/gif"),
)
MIN_IMAGE_BYTES = 500


def cover_path(ctx: AppContext, book_id: int) -> Path:
    return ctx.settings.covers_dir / f"{book_id}.img"


def media_type_of(data: bytes) -> str | None:
    return next((media for signature, media in IMAGE_SIGNATURES if data.startswith(signature)), None)


def cached_media_type(path: Path) -> str:
    with path.open("rb") as handle:
        return media_type_of(handle.read(4)) or "image/jpeg"


def drop_cover_cache(ctx: AppContext, book_id: int) -> None:
    cover_path(ctx, book_id).unlink(missing_ok=True)
    for rendered in ctx.settings.spines_dir.glob(f"{book_id}-*.png"):
        rendered.unlink(missing_ok=True)


async def cache_cover(ctx: AppContext, book_id: int) -> bool:
    """Download the book's cover URL into the cache. Returns True when a cover was written."""
    book = get_book(ctx.db, book_id)
    url = book.get("cover") or ""
    if not url.startswith("http"):
        return False
    try:
        response = await net.request("GET", url)
    except Exception as exc:
        log.info("cover fetch failed for book %s: %s", book_id, net.describe_error(exc))
        return False
    data = response.content
    if len(data) < MIN_IMAGE_BYTES or media_type_of(data) is None:
        log.info("cover for book %s was not an image (%d bytes)", book_id, len(data))
        return False
    ctx.settings.covers_dir.mkdir(parents=True, exist_ok=True)
    cover_path(ctx, book_id).write_bytes(data)
    return True
