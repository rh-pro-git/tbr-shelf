"""Local cover cache and its display-size derivatives.

The cached cover is the *source*: the bytes exactly as fetched, replaced only by an explicit
cover change or a refresh. Everything served at display size is a WebP derivative keyed by
the source's mtime, so a new source automatically retires the old derivatives.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

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
FIT_MIN_W, FIT_MAX_W = 64, 640
FIT_QUALITY = 82


def cover_path(ctx: AppContext, book_id: int) -> Path:
    return ctx.settings.covers_dir / f"{book_id}.img"


def media_type_of(data: bytes) -> str | None:
    return next((media for signature, media in IMAGE_SIGNATURES if data.startswith(signature)), None)


def cached_media_type(path: Path) -> str:
    with path.open("rb") as handle:
        return media_type_of(handle.read(4)) or "image/jpeg"


@contextmanager
def atomic_target(target: Path) -> Iterator[str]:
    """Write to a temp file beside `target`, then rename it into place: readers never see a partial
    file and a failed write leaves the old one untouched."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.fchmod(handle, 0o664)
    os.close(handle)
    try:
        yield temp
    except BaseException:
        os.unlink(temp)
        raise
    os.replace(temp, target)


def drop_cover_derivatives(ctx: AppContext, book_id: int) -> None:
    """Every file derived from the cover: spine renders and fits, and the cover's own display sizes."""
    spines, fits = ctx.settings.spines_dir, ctx.settings.cover_fits_dir
    for derived in (
        *spines.glob(f"{book_id}-*.png"),
        *spines.glob(f"{book_id}-*.webp"),
        *fits.glob(f"{book_id}-*.webp"),
    ):
        derived.unlink(missing_ok=True)


def drop_cover_cache(ctx: AppContext, book_id: int) -> None:
    cover_path(ctx, book_id).unlink(missing_ok=True)
    drop_cover_derivatives(ctx, book_id)


def clamp_fit_width(width: int) -> int:
    return max(FIT_MIN_W, min(FIT_MAX_W, width))


def fit_cover(ctx: AppContext, book_id: int, width: int) -> Path:
    """The cached cover at display size, as WebP. Cached beside the other derivatives, keyed by the
    source's mtime; stale sizes for the same book are evicted on the way through."""
    source = cover_path(ctx, book_id)
    mtime = int(source.stat().st_mtime)
    target = ctx.settings.cover_fits_dir / f"{book_id}-{width}-{mtime}.webp"
    if target.exists():
        return target
    ctx.settings.cover_fits_dir.mkdir(parents=True, exist_ok=True)
    for stale in ctx.settings.cover_fits_dir.glob(f"{book_id}-*.webp"):
        if not stale.stem.endswith(f"-{mtime}"):
            stale.unlink(missing_ok=True)
    image = Image.open(source).convert("RGB")
    image.thumbnail((width, width * 2), Image.LANCZOS)
    with atomic_target(target) as temp:
        image.save(temp, "WEBP", quality=FIT_QUALITY, method=4)
    return target


async def cache_cover(ctx: AppContext, book_id: int) -> bool:
    """Download the book's cover URL into the cache. Returns True when a cover was written.

    The new bytes land atomically and only then are the old derivatives purged, so a failed
    download changes nothing on disk."""
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
    with atomic_target(cover_path(ctx, book_id)) as temp:
        Path(temp).write_bytes(data)
    drop_cover_derivatives(ctx, book_id)
    return True
