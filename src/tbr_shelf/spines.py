"""Book spines for the shelf view.

Two kinds exist. A *local* spine is synthesized from the cached cover: the cover's
left edge stretched and blurred as texture, a curved-shading overlay, a square cover
thumbnail and a rotated title. An *external* spine is a PNG some other process
supplied through the spine-asset API, with the book's real dimensions in its
metadata; the shelf sizes the slot from those.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .context import AppContext
from .covers import atomic_target, cover_path

FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "bold": (
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    "regular": (
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
}
RENDER_SCALE = 3
SPINE_QUALITY = 85  # WebP quality for every served spine; the external PNG source is never re-encoded
SPINE_REV = 3  # bump on renderer changes; it is part of the cache key and the image URL

MIN_W, MAX_W = 24, 240
MIN_H, MAX_H = 80, 800


def clamp_size(width: int, height: int) -> tuple[int, int]:
    return max(MIN_W, min(MAX_W, width)), max(MIN_H, min(MAX_H, height))


def external_spine_path(ctx: AppContext, book_id: int) -> Path:
    return ctx.settings.external_spines_dir / f"{book_id}.png"


def spine_meta(book: dict) -> dict:
    try:
        meta = json.loads(book.get("spine_meta") or "{}")
    except json.JSONDecodeError:
        return {}
    return meta if isinstance(meta, dict) else {}


def effective_spine_source(ctx: AppContext, book: dict) -> str:
    """Which spine the shelf shows: the reader's preference, else external when one is ready."""
    preference = book.get("spine_pref") or "auto"
    if preference == "local" or not external_spine_path(ctx, book["id"]).exists():
        return "local"
    if preference == "external":
        return "external"
    return "external" if book.get("spine_state") == "ready" else "local"


def fit_external_spine(ctx: AppContext, book_id: int, css_w: int, css_h: int) -> Path:
    """Serve the external asset at display size. The stored PNG can be a multi-megabyte raster
    and the shelf paints it into a ~60x200 box, so cache a 3x-of-CSS cover-fit crop as WebP.
    Every requested size is kept; only variants of a superseded source are evicted."""
    source = external_spine_path(ctx, book_id)
    spines_dir = ctx.settings.spines_dir
    mtime = int(source.stat().st_mtime)
    target = spines_dir / f"{book_id}-external-{css_w}x{css_h}-{mtime}.webp"
    if target.exists():
        return target
    spines_dir.mkdir(parents=True, exist_ok=True)
    for stale in spines_dir.glob(f"{book_id}-external-*"):
        if not stale.stem.endswith(f"-{mtime}"):
            stale.unlink(missing_ok=True)
    width, height = css_w * RENDER_SCALE, css_h * RENDER_SCALE
    image = Image.open(source).convert("RGB")
    scale = max(width / image.width, height / image.height)
    image = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS
    )
    left, top = (image.width - width) // 2, (image.height - height) // 2
    with atomic_target(target) as temp:
        image.crop((left, top, left + width, top + height)).save(
            temp, "WEBP", quality=SPINE_QUALITY, method=4
        )
    return target


async def local_spine(ctx: AppContext, book: dict, css_w: int, css_h: int) -> Path | None:
    """Render (or reuse) the synthesized spine for a book with a cached cover."""
    cover = cover_path(ctx, book["id"])
    if not cover.exists():
        return None
    identity = hashlib.md5(f"{book['title']}|{book['author']}".encode()).hexdigest()[:8]
    key = f"{book['id']}-{css_w}x{css_h}-r{SPINE_REV}-{int(cover.stat().st_mtime)}-{identity}"
    target = ctx.settings.spines_dir / f"{key}.webp"
    if target.exists():
        return target
    ctx.settings.spines_dir.mkdir(parents=True, exist_ok=True)
    for stale in ctx.settings.spines_dir.glob(f"{book['id']}-{css_w}x{css_h}-*.webp"):
        stale.unlink(missing_ok=True)
    rendered = await asyncio.to_thread(
        render_spine, cover.read_bytes(), book["title"], book["author"], css_w, css_h
    )
    with atomic_target(target) as temp:
        Path(temp).write_bytes(rendered)
    return target


def find_font(weight: str) -> str:
    """First installed candidate for a weight. TBR_FONT_DIR, when set, is searched for NotoSans first."""
    override = os.environ.get("TBR_FONT_DIR")
    candidates = list(FONT_CANDIDATES[weight])
    if override:
        candidates.insert(
            0, str(Path(override) / f"NotoSans-{'Bold' if weight == 'bold' else 'Regular'}.ttf")
        )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"no {weight} TrueType font found; install fonts-noto-core or set TBR_FONT_DIR")


def _font(weight: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(weight), max(6, int(size)))


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    kept = text
    while kept and draw.textlength(kept + "…", font=font) > max_w:
        kept = kept[:-1]
    return (kept.rstrip() + "…") if kept else ""


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if current and draw.textlength(" ".join([*current, word]), font=font) > max_w:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) == max_lines - 1:
                break
        else:
            current.append(word)
    used = sum(len(line.split()) for line in lines)
    rest = " ".join(words[used:])
    if rest:
        lines.append(_ellipsize(draw, rest, font, max_w))
    return lines or [""]


def render_spine(cover_bytes: bytes, title: str, author: str, css_w: int, css_h: int) -> bytes:
    width, height = css_w * RENDER_SCALE, css_h * RENDER_SCALE
    cover = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
    cover_w, cover_h = cover.size

    strip = cover.crop((0, 0, max(1, int(cover_w * 0.08)), cover_h)).resize((width, height), Image.LANCZOS)
    strip = strip.filter(ImageFilter.GaussianBlur(radius=max(2, width // 14)))
    r, g, b = strip.resize((1, 1), Image.BOX).getpixel((0, 0))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if luminance > 190:  # keep very pale covers from washing out the spine
        strip = Image.eval(strip, lambda v: int(v * 0.85))
        luminance *= 0.85

    base = strip.convert("RGBA")
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    for x in range(width):  # left highlight to right shadow, like a curved spine
        t = x / max(1, width - 1)
        alpha = int(-40 * (1 - t) ** 3 + 90 * t**3)
        colour = (255, 255, 255, -alpha) if alpha < 0 else (0, 0, 0, alpha)
        shade_draw.line([(x, 0), (x, height)], fill=colour)
    base.alpha_composite(shade)

    draw = ImageDraw.Draw(base)
    pad = int(width * 0.10)
    draw.rectangle([0, 0, width - 1, int(height * 0.012)], fill=(238, 232, 214, 200))
    draw.rectangle(
        [0, height - int(height * 0.06), width - 1, height - int(height * 0.035)], fill=(0, 0, 0, 90)
    )

    thumb_w = width - 2 * pad
    side = min(cover_w, cover_h)
    thumb = cover.crop(((cover_w - side) // 2, 0, (cover_w - side) // 2 + side, side)).resize(
        (thumb_w, thumb_w), Image.LANCZOS
    )
    thumb_y = int(height * 0.035) + pad
    shadow = Image.new("RGBA", (thumb_w + 6, thumb_w + 6), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([3, 3, thumb_w + 3, thumb_w + 3], fill=(0, 0, 0, 120))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)), (pad - 3, thumb_y - 1))
    base.paste(thumb, (pad, thumb_y))

    light = luminance > 150
    title_colour = (26, 22, 16, 255) if light else (245, 241, 232, 255)
    author_colour = (26, 22, 16, 200) if light else (245, 241, 232, 195)
    label_top = thumb_y + thumb_w + pad
    available = height - int(height * 0.07) - label_top
    if available > width:
        label = Image.new("RGBA", (available, width), (0, 0, 0, 0))
        label_draw = ImageDraw.Draw(label)
        title_size = int(width * (0.30 if css_w < 40 else 0.25 if css_w < 58 else 0.22))
        max_line_w = available - int(width * 0.25)
        max_lines = 3 if css_w >= 58 else 2 if css_w >= 40 else 1
        show_author = bool(author) and css_w >= 40
        gap = int(width * 0.06)
        limit = width - int(width * 0.16)
        while True:  # shrink, then drop lines and the author, until the block fits across the spine
            title_font = _font("bold", title_size)
            author_font = _font("regular", width * 0.19)
            lines = _wrap(label_draw, title, title_font, max_line_w, max_lines)
            rows = [(line, title_font, title_colour) for line in lines]
            if show_author:
                rows.append(
                    (_ellipsize(label_draw, author, author_font, max_line_w), author_font, author_colour)
                )
            heights = [label_draw.textbbox((0, 0), text, font=font)[3] for text, font, _ in rows]
            block = sum(heights) + gap * (len(rows) - 1)
            if block <= limit:
                break
            if title_size > width * 0.18:
                title_size = int(title_size * 0.92)
            elif max_lines > 1:
                max_lines -= 1
            elif show_author:
                show_author = False
            else:
                break
        y = (width - block) // 2
        halo = (255, 255, 255, 90) if light else (0, 0, 0, 110)
        for (text, font, colour), row_h in zip(rows, heights, strict=True):
            label_draw.text((int(width * 0.05) + 1, y + 1), text, font=font, fill=halo)
            label_draw.text((int(width * 0.05), y), text, font=font, fill=colour)
            y += row_h + gap
        base.alpha_composite(label.rotate(-90, expand=True), (0, label_top))

    out = io.BytesIO()
    base.convert("RGB").save(out, "WEBP", quality=SPINE_QUALITY, method=4)
    return out.getvalue()
