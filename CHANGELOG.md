# Changelog

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
