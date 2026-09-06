# Architecture

TBR Shelf is a single-process FastAPI application over one SQLite file. There is
no job queue, no cache server and no build step: background work runs as FastAPI
background tasks inside the same process, and everything the browser needs is
served from the package.

```
browser ──HTTP──▶ FastAPI app ──▶ SQLite (library.db, WAL)
                    │   │
                    │   ├─▶ data/covers/    cover sources (+ fit/ display-size WebP)
                    │   ├─▶ data/spines/    spines as WebP (+ external/ PNG sources)
                    │   └─▶ data/audio/     synthesized summary narration
                    │
                    ├─▶ Open Library · Google Books · Audible catalog   (metadata, keyless)
                    ├─▶ any OpenAI-compatible chat endpoint             (optional)
                    ├─▶ any OpenAI-compatible speech endpoint           (optional)
                    └─▶ faster-whisper, in-process on the CPU           (optional)
```

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Settings from `TBR_*` environment variables; feature flags derived from what is configured. |
| `db.py` | Connection context manager and forward-only migrations (`schema_version` table). |
| `books.py` | Repository: reads, optimistic-locked writes, listing, dedupe keys. |
| `text.py` | Pure helpers: match keys, tag canonicalization, small formatters. |
| `catalogs.py` | The three metadata sources, candidate merging, exact-match rules. |
| `lookup.py` | The lookup task and the decision of what changes on the book (pure function). |
| `covers.py` | Cover cache: fetch once with an atomic swap, sniff the type, serve the source or a display-size WebP derivative. |
| `spines.py` | Local spine renderer (PIL), external spine fitting, effective-source rule; every served spine is WebP. |
| `llm.py`, `speech.py` | Thin clients: chat completions, TTS, lazy-loaded STT. |
| `summaries.py` | Spoiler-free summaries (publisher copy by ASIN first, the model as fallback) and similar-book suggestions. |
| `voice.py` | The librarian: prompt assembly, session history, conversation persistence, gated preferences. |
| `importers.py` | CSV rows (enriched afterwards) and pre-enriched rows (not). |
| `routes/` | HTTP surface, one router per concern (`pages.py` also serves the per-tile detail fragment). `app.py` wires them, gzip, static caching and the exception handlers. |

## Decisions that shape the code

**Optimistic concurrency, everywhere.** Every row carries a `version`; every write
sends the version it read. A stale write fails with 409 and the current version,
and the UI offers a reload. Two tabs, a phone and a background task can all touch
the same book without clobbering each other.

**Lookup fills blanks and never overwrites; refresh is the explicit exception.**
Catalog data lands only in empty fields. When the evidence is ambiguous (no
exact-title match, or the best match disagrees with the stored author) the book
is parked in `verify` with the candidates attached, and a person picks. The rule
lives in one pure function, `lookup_changes`, which is why it is easy to test.
The same function with `refresh=True` replaces the fields the catalogs own (year,
pages, format, runtime, store link) when the match has a value, never blanks one,
keeps the author rule, and leaves the chosen cover alone. Refresh then
re-downloads the cover *before* purging its derivatives, so a failed download
changes nothing.

**Sources are kept as fetched; everything served is a derivative.** The cached
cover and the external spine asset are never re-encoded. Display sizes are WebP
derivatives named by the source's mtime and cached beside it (`covers/fit/`,
`spines/`), so replacing a source retires its derivatives without bookkeeping and
any derivative can be deleted at any time. Every write goes through a temp file
and an atomic rename; fits run off the event loop and keep every requested size.

**The page is light because the detail is deferred, not paginated.** The shelf is
built in the browser from every tile's data attributes, so the list must hold
every book. Each tile therefore ships only its summary row and fetches its detail
block on first open (`GET /api/books/{id}/detail`). Shelf spines load as they
scroll into view, versioned static assets are immutable for a year, and
responses are gzipped (Starlette excludes image types; an older Starlette would
gzip them needlessly, which is wasteful but harmless).

**Covers are the reader's choice.** Lookup proposes a default cover once, when the
field is blank. Changing it afterwards is always an explicit action: pick from the
candidate strip, or from every edition Open Library knows for the work.

**Source trust is asymmetric.** Google Books rate-limits keyless callers hard, so it
contributes candidates but is excluded from the failed/not-found verdict. Open
Library and the Audible catalog decide.

**Spines are synthesized, then optionally replaced.** No public catalog serves
spine photographs. The local renderer builds a plausible spine from the cover's
edge; an external generator (any process, any model) may later PUT a real asset
with the book's physical dimensions, and the shelf sizes the slot from those. The
tracker never calls the generator; it only exposes a queue.

**Every AI feature is optional and structurally absent when unconfigured.** The
feature flags are derived from settings, not toggled; unconfigured features hide
their buttons and their endpoints answer 503. The tracker is a complete library
manager with no model running.

**Voice memory is gated on human review.** Turns are persisted, but the assistant's
prompt only ever includes preference lines that were written back through the
preferences endpoint. Whatever distills the conversations (a script, a person, a
model) must go through that door, so the assistant cannot quietly teach itself.

## Background work

Background tasks run after the response is sent, in-process. They own only their
own state fields (`lookup_state`, `summary_state`) and update against the latest
version so a concurrent edit is never reverted. Tasks do not survive a restart;
anything still `queued` at boot is marked `failed` so it can be retried by hand.

Bulk imports of pre-enriched rows deliberately queue nothing but the cover fetch.
The loose CSV path queues lookup (and summary, when a model exists) per row, which
is fine for a dozen books and not for seven hundred.

## Data directory

```
data/
  library.db                                    the library (SQLite, WAL)
  covers/{id}.img                               cover SOURCE, bytes as fetched, type sniffed on serve
  covers/fit/{id}-{w}-{mtime}.webp              the cover at display size (cover?w=)
  spines/external/{id}.png                      SOURCE delivered through PUT /api/books/{id}/spine-asset
  spines/{id}-external-{w}x{h}-{mtime}.webp     that asset fitted to a shelf slot
  spines/{id}-{w}x{h}-r{rev}-{mtime}-{hash}.webp  rendered local spine
  audio/{id}.mp3                                narration of the summary, regenerated when it changes
  preferences.json                              operator-approved preference lines for the librarian
```

Back up `library.db` (a consistent copy: `sqlite3 library.db ".backup out.db"`) and
`spines/external/`. Everything else regenerates: derivatives on the next request,
cover sources from their URLs at startup.
