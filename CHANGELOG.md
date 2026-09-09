# Changelog

## 1.3.0 — 2026-09-09

Who reads it, and what is not on the shelf yet.

- **Narrator** is a field (migration 8): filled from the author-verified Audible edition on
  lookup, replaced on Refresh, editable, shown under the byline, and part of the search on
  the page and on the server. The series is filled from the same edition only when blank.
  The enriched importer carries the narrator too.
- **Search past the library.** When a search finds nothing on the page, Audible's keyword
  search runs below the list on its own; when it finds something, one tap runs it. One box
  matches title, author and narrator. A hit already in the library shows its status and opens
  the tile; the rest add with **+ Wishlist**.
- **Add by ASIN.** `POST /api/books` with `asin` inserts that exact edition already enriched —
  narrator, series, runtime, year, cover, store link and the publisher's summary — so no
  lookup runs for it and nothing lands in verify. The same ASIN twice is a 409 naming the
  existing book; an ASIN the catalog cannot name is a 404.
- **Hear a sample.** Every book with an Audible edition, and every search hit, has a
  five-minute narration sample. `GET /api/books/{id}/sample` resolves the ASIN at tap time
  and redirects; nothing is stored. The sample and the summary narration stop each other.
- An exact-title Audible edition by a different author no longer supplies a store link,
  runtime or narrator. Candidates merged across catalogs keep narrator and series title.
- The page search folds case and punctuation on both sides, so "moby dick" finds *Moby-Dick*
  and the hit Audible tags as already yours is the one the page shows.

## 1.2.0 — 2026-09-06

Finding things in a large library, and summaries that come from the source.

- **TBR** is a status, and a virtual **TBR** tab after All gathers every book marked TBR
  across the real shelves without moving it. Blue ribbon on the shelf, blue pill in the
  list; the librarian's prompt treats TBR as the shortlist and Unread as the backlog.
- **Search as you type.** The search box sits on its own row above the filters and filters
  the rendered page on input across title, author, series and tags, with a clear button.
  Enter or Filter still submits, so a search stays a shareable URL; clearing or widening a
  server-narrowed page reloads it. The shelf view rebuilds from the visible tiles.
- The tag filter is a typeahead over every tag instead of a long dropdown.
- **Previous / next title** controls above and below an open book's summary, following the
  list's current order (skipping anything the search hid) or spine order inside the shelf
  modal.
- Summaries prefer the publisher's own copy: when a book has an Audible ASIN the summary
  task fetches it from the catalog and asks the model only when the catalog has nothing.
  A model reply that starts with UNAVAILABLE and goes on to explain itself is recorded as
  unavailable instead of being stored as the summary. Summaries still require a configured
  model; letting catalog copy through without one is a follow-up.

## 1.1.0 — 2026-09-04

Page weight and the asset pipeline. Measured on a 760-book library in headless Chrome,
fresh tab: List 74 MB / 1,553 requests → 0.33 MB / 33; Shelf 101 MB / 2,404 → 2.9 MB / 184.

- Tile detail is fetched on first open (`GET /api/books/{id}/detail`); the page ships one
  summary row per book. HTML 5.9 MB → 0.8 MB raw / 58 KB gzipped, 92k → 17k DOM nodes.
- Gzip for HTML, JSON, JS and CSS (images excluded); versioned static assets cached
  immutable for a year, unversioned ones for a day.
- Covers and spines are served as right-sized WebP derivatives (`cover?w=`), keyed by the
  source's mtime; source files are never re-encoded. Spine fits keep every requested size
  instead of evicting each other, run off the event loop, and are written atomically.
- The spine compare panel's images no longer download while the panel is closed (they
  were every page load's largest transfer); shelf spines load as they scroll into view.
- New per-book **Refresh data**: re-fetch from the catalogs and replace year, pages,
  format, runtime and store link; re-download the cover and rebuild its derivatives.
  Never blanks a field, never touches the author rule or the chosen cover; a failed
  download keeps the old copy.
- Cover cache writes are atomic, and derivatives are purged only after a successful write.
- PWA icons quantized to 256 colours (icon-512: 106 → 37 KB).

## 1.0.0 — 2026-09-02

Initial release.
