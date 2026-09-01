#!/usr/bin/env python3
"""Fill a fresh TBR Shelf with a small public-domain library for a demo or screenshots.

Cover art comes from Open Library at run time; years, page counts and the short blurbs are
hand-written. Run against a server started with an empty TBR_DATA_DIR:

    python tools/seed_demo.py --api http://127.0.0.1:8460
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request

import httpx

BOOKS: list[dict] = [
    {"title": "Frankenstein", "page_count": 280, "year": 1818, "author": "Mary Shelley", "shelf": "Audible", "status": "Finished", "tags": "Gothic; Science Fiction; Classics", "audiobook_length": "8h 35m", "finished_at": "2026-07-18",
     "summary": "A young scientist assembles a living being from dead matter and then abandons it. The creature, articulate and alone, sets out to make its maker answer for what he did."},
    {"title": "Dracula", "page_count": 418, "year": 1897, "author": "Bram Stoker", "shelf": "Audible", "status": "Reading", "tags": "Gothic; Horror; Classics", "audiobook_length": "15h 28m", "started_at": "2026-08-20",
     "summary": "Told through letters, diaries and newspaper clippings, a solicitor's trip to a Transylvanian castle sets a centuries-old predator loose in Victorian England."},
    {"title": "The Time Machine", "page_count": 118, "year": 1895, "author": "H. G. Wells", "shelf": "Audible", "status": "Finished", "tags": "Science Fiction; Classics", "audiobook_length": "3h 19m", "finished_at": "2026-05-02",
     "summary": "An inventor travels hundreds of thousands of years forward and finds humanity split into two species. What he learns about their relationship is the point of the trip."},
    {"title": "The War of the Worlds", "page_count": 192, "year": 1898, "author": "H. G. Wells", "shelf": "Audible", "status": "Unread", "tags": "Science Fiction; Classics", "audiobook_length": "6h 33m",
     "summary": "Cylinders land in the English countryside and what climbs out is not interested in negotiation. A narrator on foot watches a confident empire discover it is not the top of the food chain."},
    {"title": "Twenty Thousand Leagues Under the Sea", "page_count": 400, "year": 1870, "author": "Jules Verne", "shelf": "Audible", "status": "Unread", "tags": "Adventure; Science Fiction; Classics", "audiobook_length": "13h 52m",
     "summary": "A marine biologist hunting a sea monster is taken aboard the submarine Nautilus and its brilliant, bitter captain, who has renounced the surface world entirely."},
    {"title": "The Count of Monte Cristo", "page_count": 1276, "year": 1844, "author": "Alexandre Dumas", "shelf": "Audible", "status": "Paused", "tags": "Adventure; Classics; Revenge", "audiobook_length": "52h 41m", "started_at": "2026-03-11",
     "summary": "A sailor betrayed on the eve of his wedding spends years in an island prison, escapes with a fortune, and returns under a new name to take his enemies apart one by one."},
    {"title": "Pride and Prejudice", "page_count": 432, "year": 1813, "author": "Jane Austen", "shelf": "Chirp", "status": "Finished", "tags": "Romance; Classics; Comedy of Manners", "audiobook_length": "11h 35m", "finished_at": "2026-01-29",
     "summary": "The second of five Bennet sisters meets a wealthy, reserved newcomer and dislikes him immediately. Both of them turn out to be wrong about nearly everything, including each other."},
    {"title": "Jane Eyre", "page_count": 532, "year": 1847, "author": "Charlotte Brontë", "shelf": "Chirp", "status": "Unread", "tags": "Gothic; Romance; Classics", "audiobook_length": "19h 10m",
     "summary": "An orphaned governess takes a post at a remote house whose master is charming, moody and hiding something upstairs. She insists on her own terms throughout."},
    {"title": "Wuthering Heights", "page_count": 416, "year": 1847, "author": "Emily Brontë", "shelf": "Chirp", "status": "Unread", "tags": "Gothic; Classics", "audiobook_length": "11h 19m",
     "summary": "Two households on the Yorkshire moors are bound together by a foundling boy and the girl he grows up beside. The consequences run across two generations."},
    {"title": "The Picture of Dorian Gray", "page_count": 254, "year": 1890, "author": "Oscar Wilde", "shelf": "Chirp", "status": "Reading", "tags": "Gothic; Philosophy; Classics", "audiobook_length": "8h 55m", "started_at": "2026-08-28",
     "summary": "A beautiful young man wishes his portrait would age instead of him. It does, and it records considerably more than the years."},
    {"title": "Treasure Island", "page_count": 240, "year": 1883, "author": "Robert Louis Stevenson", "shelf": "Chirp", "status": "Finished", "tags": "Adventure; Pirates; Classics", "audiobook_length": "7h 6m", "finished_at": "2025-12-14",
     "summary": "An innkeeper's son finds a map in a dead sailor's chest and joins a voyage to find the treasure. The ship's cook has a parrot, one leg, and plans of his own."},
    {"title": "The Adventures of Sherlock Holmes", "page_count": 307, "year": 1892, "author": "Arthur Conan Doyle", "shelf": "Wishlist", "status": "Unread", "tags": "Mystery; Short Stories; Classics",
     "summary": "Twelve cases for the consulting detective of Baker Street, narrated by the doctor who shares his rooms and rarely keeps up."},
    {"title": "Moby-Dick", "page_count": 720, "year": 1851, "author": "Herman Melville", "shelf": "Wishlist", "status": "Unread", "tags": "Adventure; Classics; Sea Stories",
     "summary": "A schoolteacher signs onto a whaling ship whose captain has one purpose left. Between the chapters of the hunt, everything there is to know about whales."},
    {"title": "The Odyssey", "page_count": 541, "author": "Homer", "shelf": "Wishlist", "status": "Unread", "tags": "Epic; Mythology; Classics",
     "summary": "After ten years of war, a king tries to get home and it takes another ten. Meanwhile his house fills with men who assume he is dead."},
    {"title": "Little Women", "page_count": 449, "year": 1868, "author": "Louisa May Alcott", "shelf": "Wishlist", "status": "Unread", "tags": "Coming of Age; Classics; Family",
     "summary": "Four sisters grow up in genteel poverty while their father is away at war, each pulling toward a different idea of what a life should be."},
    {"title": "Around the World in Eighty Days", "page_count": 256, "year": 1872, "author": "Jules Verne", "shelf": "Wishlist", "status": "Unread", "tags": "Adventure; Classics; Travel",
     "summary": "A precise English gentleman bets his club he can circle the globe in eighty days and leaves that evening with his newly hired valet."},
    {"title": "The Call of the Wild", "page_count": 232, "year": 1903, "author": "Jack London", "shelf": "Audible", "status": "Finished", "tags": "Adventure; Classics; Animals", "audiobook_length": "3h 12m", "finished_at": "2026-06-09",
     "summary": "A pampered California dog is stolen and sold north as a sled dog during the Klondike gold rush. Each master teaches him something, and the wilderness teaches him the rest."},
    {"title": "The Invisible Man", "page_count": 208, "year": 1897, "author": "H. G. Wells", "shelf": "Chirp", "status": "Unread", "tags": "Science Fiction; Classics", "audiobook_length": "5h 6m",
     "summary": "A stranger wrapped in bandages takes a room at a village inn and asks to be left alone. He has discovered how to make himself invisible and not how to undo it."},
    {"title": "A Study in Scarlet", "page_count": 188, "year": 1887, "author": "Arthur Conan Doyle", "shelf": "Wishlist", "status": "Unread", "tags": "Mystery; Classics",
     "summary": "A wounded army doctor back from Afghanistan needs a flatmate and is introduced to an eccentric who claims to solve crimes by reasoning. A body in an empty house gives him the chance to prove it."},
]  # fmt: skip

OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"


async def enrich(client: httpx.AsyncClient, book: dict) -> dict:
    params = {
        "title": book["title"],
        "author": book["author"],
        "limit": 5,
        "fields": "title,cover_edition_key,cover_i",
    }
    response = await client.get(OPEN_LIBRARY_SEARCH, params=params)
    response.raise_for_status()
    docs = response.json().get("docs") or []
    doc = next((d for d in docs if d.get("cover_edition_key") or d.get("cover_i")), docs[0] if docs else {})
    if doc.get("cover_edition_key"):
        cover = f"https://covers.openlibrary.org/b/olid/{doc['cover_edition_key']}-L.jpg"
    elif doc.get("cover_i"):
        cover = f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg"
    else:
        cover = ""
    return {
        **book,
        "cover": cover,
    }


async def build(delay_s: float) -> list[dict]:
    records = []
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "tbr-shelf-seed/1.0"}) as client:
        for book in BOOKS:
            records.append(await enrich(client, book))
            record = records[-1]
            print(f"  {book['title']}: cover={'yes' if record['cover'] else 'no'}")
            await asyncio.sleep(delay_s)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8460", help="TBR Shelf base URL")
    parser.add_argument("--delay", type=float, default=0.5, help="pause between Open Library calls")
    args = parser.parse_args()
    print("Resolving covers and years from Open Library…")
    records = asyncio.run(build(args.delay))
    request = urllib.request.Request(
        f"{args.api}/api/import/books",
        data=json.dumps(records).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    print(f"Imported {result['added']} book(s), {result['skipped_duplicates']} duplicate(s) skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
