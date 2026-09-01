"""Metadata sources and the matching rules that decide what is trustworthy.

Three catalogs are queried in parallel:

* Open Library, by title only. The stored author is deliberately kept out of the
  query so a wrong author cannot hide the real book; it is verified afterwards.
* Google Books, keyless. It rate-limits aggressively, so it contributes
  candidates but never decides the verdict.
* The Audible catalog API, keyless. The audiobook-native source: it carries
  independent titles the others lack and supplies cover art, runtime and the ASIN.
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import net
from .text import format_runtime, match_key, pick_print_format, year_from

Candidate = dict[str, Any]

OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
OPEN_LIBRARY_COVER_BY_OLID = "https://covers.openlibrary.org/b/olid/{olid}-L.jpg"
OPEN_LIBRARY_COVER_BY_ID = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
GOOGLE_BOOKS_VOLUMES = "https://www.googleapis.com/books/v1/volumes"
AUDIBLE_CATALOG = "https://api.audible.com/1.0/catalog/products"

VERDICT_SOURCES = ("Open Library", "Audible")
MERGEABLE_FIELDS = ("page_count", "physical_format", "year", "cover", "asin", "minutes", "audible_cover")


def _source_status(result: object) -> str:
    return net.describe_error(result) if isinstance(result, Exception) else "ok"


async def open_library_candidates(title: str) -> list[Candidate]:
    response = await net.request(
        "GET",
        OPEN_LIBRARY_SEARCH,
        params={
            "title": title,
            "limit": 8,
            "fields": "title,author_name,first_publish_year,cover_i,cover_edition_key,"
            "edition_count,number_of_pages_median,format",
        },
    )
    candidates = []
    for doc in response.json().get("docs", []):
        # cover_edition_key is Open Library's canonical display edition: the familiar cover,
        # not whichever scan happens to sort first.
        if doc.get("cover_edition_key"):
            cover = OPEN_LIBRARY_COVER_BY_OLID.format(olid=doc["cover_edition_key"])
        elif doc.get("cover_i"):
            cover = OPEN_LIBRARY_COVER_BY_ID.format(cover_id=doc["cover_i"])
        else:
            cover = ""
        candidates.append(
            {
                "title": doc.get("title", ""),
                "author": ", ".join((doc.get("author_name") or [])[:3]),
                "year": doc.get("first_publish_year"),
                "cover": cover,
                "page_count": doc.get("number_of_pages_median") or None,
                "physical_format": pick_print_format(doc.get("format")),
                "editions": doc.get("edition_count") or 0,
                "source": "Open Library",
            }
        )
    return candidates


async def google_books_candidates(title: str, author: str) -> list[Candidate]:
    query = f'intitle:"{title}"' + (f' inauthor:"{author}"' if author else "")
    response = await net.request("GET", GOOGLE_BOOKS_VOLUMES, params={"q": query, "maxResults": 5})
    candidates = []
    for item in response.json().get("items", []):
        info = item.get("volumeInfo", {})
        thumbnail = info.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
        candidates.append(
            {
                "title": info.get("title", ""),
                "author": ", ".join(info.get("authors", [])[:3]),
                "year": year_from(info.get("publishedDate")),
                "cover": thumbnail,
                "page_count": info.get("pageCount"),
                "editions": 0,
                "source": "Google Books",
            }
        )
    return candidates


async def audible_candidates(title: str, author: str) -> list[Candidate]:
    """Two queries: title+author surfaces the right edition of a generic title, title-only keeps
    the wrong-stored-author safety net. English, unabridged editions sort first so ties pick them."""
    base = {
        "title": title,
        "num_results": 5,
        "response_groups": "media,contributors,product_desc,product_attrs",
    }
    queries = [base, {**base, "author": author}] if author else [base]
    responses = await asyncio.gather(*[net.request("GET", AUDIBLE_CATALOG, params=q) for q in queries])
    candidates = []
    seen: set[str] = set()
    for response in responses:
        for product in response.json().get("products", []):
            asin = product.get("asin")
            if not asin or not product.get("title") or asin in seen:
                continue
            if (product.get("language") or "english").lower() != "english":
                continue
            seen.add(asin)
            cover = (product.get("product_images") or {}).get("500", "")
            candidates.append(
                {
                    "title": product["title"],
                    "author": ", ".join(a.get("name", "") for a in (product.get("authors") or [])[:3]),
                    "year": year_from(product.get("release_date")),
                    "cover": cover,
                    "audible_cover": cover,
                    "page_count": None,
                    "editions": 0,
                    "source": "Audible",
                    "asin": asin,
                    "minutes": product.get("runtime_length_min"),
                    "abridged": (product.get("format_type") or "") == "abridged",
                }
            )
    candidates.sort(key=lambda c: c["abridged"])
    return candidates


def merge_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Collapse the same title+author from different sources into one, keeping the first
    seen and filling its blanks from later duplicates. Order (relevance) is preserved."""
    merged: dict[tuple[str, str], Candidate] = {}
    ordered: list[Candidate] = []
    for candidate in candidates:
        if not candidate["title"]:
            continue
        key = (match_key(candidate["title"]), match_key(candidate["author"]))
        if key in merged:
            existing = merged[key]
            for field in MERGEABLE_FIELDS:
                if not existing.get(field) and candidate.get(field):
                    existing[field] = candidate[field]
        else:
            merged[key] = candidate
            ordered.append(candidate)
    return ordered


async def search_candidates(title: str, author: str = "") -> tuple[list[Candidate], dict[str, str]]:
    results = await asyncio.gather(
        open_library_candidates(title),
        google_books_candidates(title, author),
        audible_candidates(title, author),
        return_exceptions=True,
    )
    sources = dict(
        zip(("Open Library", "Google Books", "Audible"), map(_source_status, results), strict=True)
    )
    found = [c for result in results if not isinstance(result, BaseException) for c in result]
    return merge_candidates(found), sources


def authors_overlap(stored: str, candidate: str) -> bool:
    a, b = match_key(stored), match_key(candidate)
    return bool(a) and (a in b or b in a)


def match_exact(book: dict, candidates: list[Candidate]) -> Candidate | None:
    """The best candidate whose title matches exactly, preferring an author match.

    Edition count breaks ties among exact-title matches only. It never reorders the raw
    list, where a heavily reprinted wrong book (a study guide, say) would outrank the real one.
    """
    exact = [c for c in candidates if match_key(c["title"]) == match_key(book["title"])]
    if book.get("author"):
        by_author = [c for c in exact if authors_overlap(book["author"], c.get("author", ""))]
        if by_author:
            exact = by_author
    return max(exact, key=lambda c: c.get("editions") or 0) if exact else None


def runtime_text(candidate: Candidate) -> str:
    minutes = candidate.get("minutes")
    return format_runtime(minutes) if isinstance(minutes, int) else ""


async def open_library_edition_covers(title: str, author: str, max_works: int = 2) -> list[str]:
    """Every cover across the editions of the author-verified Open Library work(s) for a title."""
    response = await net.request(
        "GET", OPEN_LIBRARY_SEARCH, params={"title": title, "limit": 5, "fields": "key,title,author_name"}
    )
    wanted_author = match_key(author)
    works = [
        doc["key"]
        for doc in response.json().get("docs", [])
        if doc.get("key", "").startswith("/works/")
        and match_key(doc.get("title", "")) == match_key(title)
        and (not wanted_author or any(authors_overlap(author, name) for name in doc.get("author_name") or []))
    ][:max_works]
    covers: list[str] = []
    for work_key in works:
        editions = await net.request(
            "GET", f"https://openlibrary.org{work_key}/editions.json", params={"limit": 50}
        )
        for edition in editions.json().get("entries", []):
            cover_id = next((c for c in (edition.get("covers") or []) if isinstance(c, int) and c > 0), None)
            if cover_id:
                covers.append(OPEN_LIBRARY_COVER_BY_ID.format(cover_id=cover_id))
    return covers
