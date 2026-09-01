"""Spoiler-free summaries and similar-book suggestions from the language model."""

from __future__ import annotations

import json
import logging
import re

from . import llm
from .books import get_book, update_book_latest
from .context import AppContext

log = logging.getLogger(__name__)

UNAVAILABLE_TOKEN = "UNAVAILABLE"


def audio_path(ctx: AppContext, book_id: int):
    return ctx.settings.audio_dir / f"{book_id}.mp3"


def drop_audio(ctx: AppContext, book_id: int) -> None:
    audio_path(ctx, book_id).unlink(missing_ok=True)


def summary_prompt(book: dict) -> str:
    return (
        "Write a concise factual spoiler-free 2-3 sentence book summary. "
        f"Title: {book['title']}; Author: {book['author'] or 'unknown'}. "
        f"If not confidently identifiable, output exactly {UNAVAILABLE_TOKEN}."
    )


async def run_summary(ctx: AppContext, book_id: int) -> None:
    book = get_book(ctx.db, book_id)
    if book.get("summary"):
        return
    try:
        text = await llm.chat(
            ctx, [{"role": "user", "content": summary_prompt(book)}], temperature=0.4, max_tokens=700
        )
    except llm.LLMUnavailable as exc:
        log.info("summary unavailable for book %s: %s", book_id, exc)
        update_book_latest(ctx.db, book_id, {"summary_state": "failed"})
        return
    usable = bool(text) and text != UNAVAILABLE_TOKEN
    update_book_latest(
        ctx.db,
        book_id,
        {"summary": text if usable else "", "summary_state": "ready" if usable else "unavailable"},
    )
    drop_audio(ctx, book_id)


def similar_prompt(book: dict) -> str:
    return (
        f"Suggest exactly 3 books similar to {book['title']} by {book['author'] or 'unknown'}. "
        "Return JSON only: a list of objects with title and author. "
        "These are suggestions only, never claims of user ownership."
    )


def parse_suggestions(text: str) -> list[dict]:
    match = re.search(r"\[[\s\S]*\]", text)
    items = json.loads(match.group(0) if match else text)
    return [
        {"title": str(item.get("title", ""))[:500], "author": str(item.get("author", ""))[:500]}
        for item in items
        if isinstance(item, dict) and item.get("title")
    ][:3]


async def suggest_similar(ctx: AppContext, book: dict) -> list[dict]:
    text = await llm.chat(
        ctx, [{"role": "user", "content": similar_prompt(book)}], temperature=0.5, max_tokens=500
    )
    return parse_suggestions(text)
