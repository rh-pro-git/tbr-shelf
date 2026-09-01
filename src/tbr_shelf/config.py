"""Runtime configuration, read once from the environment.

Every AI-facing backend is optional. When its URL is unset the corresponding
feature is hidden in the UI and its endpoints answer 503, so the tracker is fully
usable as a plain library manager with no model running anywhere.
"""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

SHELVES: tuple[str, ...] = ("Audible", "Chirp", "Wishlist")
STATUSES: tuple[str, ...] = ("Unread", "Reading", "Finished", "Paused")
PRINT_FORMATS: tuple[str, ...] = ("Hardcover", "Paperback", "Mass Market")
SPINE_PREFERENCES: tuple[str, ...] = ("auto", "external", "local")

DEFAULT_LLM_EXTRA_BODY = '{"chat_template_kwargs": {"enable_thinking": false}}'


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"TBR_{name}", default).strip()


def _json_object(raw: str, name: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"TBR_{name} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"TBR_{name} must be a JSON object")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_url: str = ""
    llm_model: str = ""
    llm_api_key: str = "not-needed"
    llm_extra_body: dict = field(default_factory=dict)
    tts_url: str = ""
    tts_model: str = "kokoro"
    tts_voice: str = "af_heart"
    stt_model: str = "small"
    request_timeout_s: float = 180.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def spines_dir(self) -> Path:
        return self.data_dir / "spines"

    @property
    def external_spines_dir(self) -> Path:
        return self.data_dir / "spines" / "external"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def preferences_path(self) -> Path:
        return self.data_dir / "preferences.json"

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_url)

    @property
    def has_tts(self) -> bool:
        return bool(self.tts_url)

    @property
    def has_stt(self) -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    @property
    def has_voice_chat(self) -> bool:
        return self.has_llm and self.has_stt

    def feature_flags(self) -> dict[str, bool]:
        return {"llm": self.has_llm, "tts": self.has_tts, "voice": self.has_voice_chat}


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(_env("DATA_DIR", "data")).expanduser().resolve(),
        llm_url=_env("LLM_URL"),
        llm_model=_env("LLM_MODEL"),
        llm_api_key=_env("LLM_API_KEY", "not-needed"),
        llm_extra_body=_json_object(_env("LLM_EXTRA_BODY", DEFAULT_LLM_EXTRA_BODY), "LLM_EXTRA_BODY"),
        tts_url=_env("TTS_URL"),
        tts_model=_env("TTS_MODEL", "kokoro"),
        tts_voice=_env("TTS_VOICE", "af_heart"),
        stt_model=_env("STT_MODEL", "small"),
        request_timeout_s=float(_env("REQUEST_TIMEOUT_S", "180")),
    )
