"""Pure text helpers: matching keys, tag canonicalization, small formatters."""

from __future__ import annotations

import re
import unicodedata

PRINT_FORMAT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("mass market", "Mass Market"),
    ("hardcover", "Hardcover"),
    ("hardback", "Hardcover"),
    ("paperback", "Paperback"),
    ("trade", "Paperback"),
)

_TAG_SEPARATORS = re.compile(r"[;,/]")
_NON_ALNUM = re.compile("[^a-z0-9]+")


def match_key(value: str) -> str:
    """Fold a title or author to a comparison key: ASCII, lowercase, alphanumerics only.

    Accented authors ("Márquez") and punctuation variants ("Don't" / "Dont") must
    compare equal or exact-title matching silently fails on real catalog data.
    """
    folded = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return _NON_ALNUM.sub("", folded.lower())


def canonical_tag(tag: str) -> str:
    """Title-case each word of a tag, keeping acronyms ("SF") and hyphenated parts."""

    def cap(segment: str) -> str:
        if not segment.isalpha() or segment.isupper():
            return segment
        return segment[:1].upper() + segment[1:].lower()

    return " ".join("-".join(cap(part) for part in word.split("-")) for word in tag.split())


def normalize_tags(raw: str | None) -> str:
    """Split on `; , /`, canonicalize, de-duplicate case-insensitively, rejoin with ', '."""
    tags: list[str] = []
    seen: set[str] = set()
    for piece in _TAG_SEPARATORS.split(raw or ""):
        tag = canonical_tag(" ".join(piece.split()))
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)
    return ", ".join(tags)


def split_tags(stored: str | None) -> list[str]:
    return [tag.strip() for tag in (stored or "").split(",") if tag.strip()]


def pick_print_format(formats: list[str] | None) -> str | None:
    """Map a catalog's free-text edition formats onto the three print tiers the shelf sizes by."""
    for keyword, tier in PRINT_FORMAT_KEYWORDS:
        if any(keyword in (fmt or "").lower() for fmt in formats or []):
            return tier
    return None


def format_runtime(minutes: int | None) -> str:
    if not minutes:
        return ""
    return f"{minutes // 60}h {minutes % 60}m"


def year_from(date_text: str | None) -> int | None:
    match = re.match(r"^(\d{4})", date_text or "")
    return int(match.group(1)) if match else None


def is_iso_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def is_asin(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9]{10}", value))


def audible_product_url(asin: str) -> str:
    return f"https://www.audible.com/pd/{asin}"


def asin_from_url(url: str | None) -> str | None:
    match = re.search(r"/pd/([A-Za-z0-9]{10})", url or "")
    return match.group(1) if match else None
