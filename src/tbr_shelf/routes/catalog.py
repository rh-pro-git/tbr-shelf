"""Past the library: Audible's keyword search, each hit tagged when it is already here."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .. import catalogs, net
from ..books import tag_owned
from ..context import AppContext
from . import get_ctx

router = APIRouter(prefix="/api")

MIN_QUERY_CHARS = 2


@router.get("/search")
async def search(q: str = "", ctx: AppContext = Depends(get_ctx)) -> dict:
    """One box that matches title, author and narrator. A hit carries `book_id` when the library
    already holds that edition, or the same title by the same author, so the page offers "open"."""
    query = " ".join(q.split())
    if len(query) < MIN_QUERY_CHARS:
        return {"q": query, "results": []}
    try:
        results = await catalogs.audible_search(query)
    except (TimeoutError, httpx.HTTPError) as exc:
        raise HTTPException(503, f"Audible is not answering ({net.describe_error(exc)}).") from exc
    tag_owned(ctx.db, results)
    return {"q": query, "results": results}
