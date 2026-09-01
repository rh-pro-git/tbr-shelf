"""Per-application state shared by routes and background tasks."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .db import Database

VOICE_SESSION_CAP = 20


@dataclass
class AppContext:
    settings: Settings
    db: Database
    voice_sessions: dict[str, list[dict]] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> AppContext:
        return cls(settings=settings, db=Database(settings.db_path))
