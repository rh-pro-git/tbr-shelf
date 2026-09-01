#!/usr/bin/env python3
"""Import an Audible library into TBR Shelf from an Audible Library Extractor export.

The browser extension's export is thin (some versions cannot reach store pages at all),
but every row carries an ASIN. Each book is therefore re-fetched by ASIN from the keyless
Audible catalog API, which is exact rather than a title search: the rows land verified,
with year, categories, runtime, cover art and the publisher's own summary, and need no
lookup pass and no model-generated summary.

    python tools/import_audible_library.py export.json --dry-run
    python tools/import_audible_library.py export.json --api http://127.0.0.1:8460
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

CATALOG = "https://api.audible.com/1.0/catalog/products"
RESPONSE_GROUPS = "media,contributors,product_desc,product_attrs,product_extended_attrs,category_ladders"
DEFAULT_STATE = Path.home() / ".cache" / "tbr-shelf" / "audible-import"
MAX_TAGS = 5
BATCH = 250
EXCLUDED_COLLECTIONS = ("__ARCHIVE",)


def log(message: str) -> None:
    print(f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {message}", flush=True)


def get_json(url: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "tbr-shelf-import/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def post_json(url: str, payload: object) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def fetch_product(asin: str) -> dict | None:
    for attempt in range(3):
        try:
            return get_json(f"{CATALOG}/{asin}?response_groups={RESPONSE_GROUPS}")["product"]
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError):
            if attempt == 2:
                return None
            time.sleep(1)
    return None


def load_catalog(asins: list[str], cache_path: Path, refresh: bool) -> dict[str, dict | None]:
    cache: dict[str, dict | None] = {}
    if cache_path.exists() and not refresh:
        cache = json.loads(cache_path.read_text())
    missing = [asin for asin in asins if asin not in cache]
    if missing:
        log(f"catalog: fetching {len(missing)} product(s)")
        for count, asin in enumerate(missing, 1):
            cache[asin] = fetch_product(asin)
            if count % 100 == 0:
                log(f"  {count}/{len(missing)}")
            time.sleep(0.05)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    resolved = sum(1 for asin in asins if cache.get(asin))
    log(f"catalog: {resolved}/{len(asins)} resolved")
    return cache


def strip_html(text: str) -> str:
    text = re.sub(r"<(br|/p|/div)[^>]*>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def tags_from(product: dict) -> str:
    """The top rung of every category ladder first, then each leaf, capped."""
    tags: list[str] = []
    seen: set[str] = set()
    ladders = product.get("category_ladders") or []
    for rung in (0, -1):
        for ladder in ladders:
            rungs = ladder.get("ladder") or []
            if not rungs:
                continue
            # Audible categories contain commas ("Mystery, Thriller & Suspense"); split them here so
            # the tracker's own comma splitting does not fracture them past the cap.
            for name in (part.strip() for part in (rungs[rung].get("name") or "").split(",")):
                key = name.casefold()
                if name and key not in seen:
                    seen.add(key)
                    tags.append(name)
    return "; ".join(tags[:MAX_TAGS])


def series_of(book: dict) -> str | None:
    series = book.get("series") or []
    if not series:
        return None
    name = (series[0].get("name") or "").strip()
    numbers = [str(n).strip() for n in (series[0].get("bookNumbers") or []) if str(n).strip()]
    if not name:
        return None
    return f"{name}, Book {numbers[0]}" if numbers else name


def status_of(book: dict) -> str:
    progress = book.get("progress")
    if isinstance(progress, str):  # "Finished" or "Xh Ym left"
        return "Finished" if progress.strip() == "Finished" else "Reading"
    return "Unread"


def to_record(book: dict, product: dict | None) -> dict:
    product = product or {}
    wishlist = "__WISHLIST" in (book.get("collectionIds") or [])
    authors = [
        a.get("name", "").strip()
        for a in (product.get("authors") or book.get("authors") or [])
        if a.get("name")
    ]
    minutes = product.get("runtime_length_min")
    release = product.get("release_date") or ""
    year_match = re.match(r"^\d{4}", release)
    return {
        "asin": book["asin"],
        "title": (product.get("title") or book["title"]).strip()[:500],
        "author": ", ".join(authors[:3])[:500],
        "series": series_of(book),
        "shelf": "Wishlist" if wishlist else "Audible",
        "status": "Unread" if wishlist else status_of(book),
        "tags": tags_from(product),
        "year": int(year_match.group(0)) if year_match else None,
        "audiobook_length": f"{minutes // 60}h {minutes % 60}m"
        if minutes
        else (book.get("length") or "").strip(),
        "cover": (product.get("product_images") or {}).get("500") or "",
        "summary": strip_html(product.get("publisher_summary") or ""),
    }


def build_records(export_path: Path, cache_path: Path, refresh: bool) -> list[dict]:
    books = json.loads(export_path.read_text())["books"]
    kept = [
        b
        for b in books
        if not b.get("podcastParent") and not set(b.get("collectionIds") or []) & set(EXCLUDED_COLLECTIONS)
    ]
    excluded = len(books) - len(kept)
    log(f"export: {len(books)} book(s), {len(kept)} in scope ({excluded} archived/podcast excluded)")
    catalog = load_catalog([b["asin"] for b in kept], cache_path, refresh)
    return [to_record(b, catalog.get(b["asin"])) for b in kept]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("export", type=Path, help="Audible Library Extractor raw JSON export")
    parser.add_argument("--api", default="http://127.0.0.1:8460", help="TBR Shelf base URL")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="cache and manifest directory")
    parser.add_argument(
        "--dry-run", action="store_true", help="build and report, write nothing to the tracker"
    )
    parser.add_argument("--limit", type=int, default=0, help="only import the first N records")
    parser.add_argument("--batch", type=int, default=BATCH, help="records per request")
    parser.add_argument("--refresh-cache", action="store_true", help="re-fetch every catalog product")
    args = parser.parse_args()

    args.state.mkdir(parents=True, exist_ok=True)
    records = build_records(args.export, args.state / "catalog-cache.json", args.refresh_cache)
    if args.limit:
        records = records[: args.limit]

    filled = {
        f: sum(1 for r in records if r[f])
        for f in ("series", "tags", "year", "audiobook_length", "cover", "summary")
    }
    log(f"records: {len(records)} | " + " ".join(f"{k}={v}" for k, v in filled.items()))
    thin = [r["title"][:34] for r in records if not r["summary"] or not r["year"]]
    if thin:
        log(f"records: {len(thin)} missing year or summary, e.g. {thin[:8]}")

    if args.dry_run:
        out = args.state / "dry-run.json"
        out.write_text(json.dumps(records, indent=1))
        log(f"dry-run: wrote {out}")
        return 0

    added = skipped = 0
    ids: list[int] = []
    for start in range(0, len(records), args.batch):
        chunk = records[start : start + args.batch]
        try:
            result = post_json(f"{args.api}/api/import/books", chunk)
        except urllib.error.HTTPError as exc:
            log(f"batch {start // args.batch + 1}: HTTP {exc.code} {exc.read()[:200]!r}")
            return 1
        added += result["added"]
        skipped += result["skipped_duplicates"]
        ids += result["ids"]
        batch_no = start // args.batch + 1
        log(f"batch {batch_no}: +{result['added']} added, {result['skipped_duplicates']} duplicate(s)")

    manifest = args.state / f"imported-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    manifest.write_text(
        json.dumps({"ids": ids, "added": added, "skipped": skipped, "export": str(args.export)}, indent=1)
    )
    log(f"done: {added} added, {skipped} duplicate(s) skipped; manifest {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
