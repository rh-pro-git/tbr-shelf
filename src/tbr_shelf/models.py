"""Request bodies. Everything arriving over HTTP is validated here before it touches the database."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Shelf = Literal["Audible", "Chirp", "Wishlist"]
SpinePreference = Literal["auto", "external", "local"]


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(default="", max_length=500)
    shelf: Shelf = "Wishlist"
    status: str = "Unread"
    series: str | None = None
    tags: str = ""
    notes: str = ""


class BookUpdate(BaseModel):
    version: int
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=500)
    series: str | None = None
    shelf: Shelf | None = None
    status: str | None = None
    tags: str | None = None
    notes: str | None = None
    cover: str | None = None
    year: int | None = None
    page_count: int | None = None
    audiobook_length: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    store_url: str | None = None
    physical_format: str | None = None
    spine_pref: SpinePreference | None = None


class VersionAction(BaseModel):
    version: int


class CandidateAccept(BaseModel):
    version: int
    candidate: dict


class ImportedBook(BaseModel):
    """A row that arrives already enriched, so no lookup or summary is queued for it."""

    title: str = Field(min_length=1, max_length=500)
    author: str = Field(default="", max_length=500)
    asin: str | None = Field(default=None, min_length=10, max_length=10)
    series: str | None = None
    shelf: Shelf = "Audible"
    status: str = "Unread"
    tags: str = ""
    notes: str = ""
    year: int | None = None
    page_count: int | None = None
    physical_format: str | None = None
    audiobook_length: str = ""
    cover: str = ""
    summary: str = ""
    store_url: str | None = None
    date_added: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class PreferencesUpdate(BaseModel):
    lines: list[str] = Field(max_length=100)
