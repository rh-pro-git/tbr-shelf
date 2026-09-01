"""Application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import net
from .books import BookNotFound, VersionConflict
from .config import Settings, load_settings
from .context import AppContext
from .covers import cache_cover, cover_path
from .llm import LLMUnavailable
from .routes import books, imports, media, pages, voice
from .routes.pages import STATIC_DIR
from .speech import SpeechUnavailable

log = logging.getLogger(__name__)

COVER_SWEEP_PAUSE_S = 0.3


def reset_orphaned_states(ctx: AppContext) -> None:
    """Background tasks do not survive a restart; anything still queued at boot is orphaned."""
    with ctx.db.transaction() as connection:
        connection.execute("UPDATE books SET lookup_state='failed' WHERE lookup_state='queued'")
        connection.execute("UPDATE books SET summary_state='failed' WHERE summary_state='queued'")


async def sweep_missing_covers(ctx: AppContext) -> None:
    with ctx.db.transaction() as connection:
        ids = [
            row[0] for row in connection.execute("SELECT id FROM books WHERE cover LIKE 'http%'").fetchall()
        ]
    for book_id in ids:
        if not cover_path(ctx, book_id).exists():
            await cache_cover(ctx, book_id)
            await asyncio.sleep(COVER_SWEEP_PAUSE_S)


def create_app(settings: Settings | None = None, *, sweep_covers: bool = True) -> FastAPI:
    settings = settings or load_settings()
    ctx = AppContext.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        net.configure(settings.request_timeout_s)
        ctx.db.migrate()
        reset_orphaned_states(ctx)
        sweep = asyncio.create_task(sweep_missing_covers(ctx)) if sweep_covers else None
        log.info("TBR Shelf ready: data=%s features=%s", settings.data_dir, settings.feature_flags())
        try:
            yield
        finally:
            if sweep is not None:
                sweep.cancel()
            await net.close()

    app = FastAPI(title="TBR Shelf", lifespan=lifespan)
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for router in (pages.router, books.router, media.router, imports.router, voice.router):
        app.include_router(router)

    @app.exception_handler(BookNotFound)
    async def _not_found(_request: Request, _exc: BookNotFound) -> JSONResponse:
        return JSONResponse({"detail": "Book not found"}, status_code=404)

    @app.exception_handler(VersionConflict)
    async def _conflict(_request: Request, exc: VersionConflict) -> JSONResponse:
        return JSONResponse(
            {"detail": {"message": str(exc), "current_version": exc.current_version}}, status_code=409
        )

    @app.exception_handler(LLMUnavailable)
    async def _llm_down(_request: Request, exc: LLMUnavailable) -> JSONResponse:
        return JSONResponse(
            {"detail": f"The language model is unavailable ({exc}). Try again."}, status_code=503
        )

    @app.exception_handler(SpeechUnavailable)
    async def _speech_down(_request: Request, exc: SpeechUnavailable) -> JSONResponse:
        return JSONResponse({"detail": f"Speech is unavailable ({exc})."}, status_code=503)

    @app.get("/healthz")
    async def health() -> dict:
        return {"ok": True, "schema_version": ctx.db.schema_version(), "features": settings.feature_flags()}

    return app
