"""Text-to-speech (OpenAI-compatible /audio/speech) and speech-to-text (faster-whisper, optional)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from . import net
from .context import AppContext


class SpeechUnavailable(RuntimeError):
    pass


async def synthesize(ctx: AppContext, text: str) -> bytes:
    settings = ctx.settings
    if not settings.has_tts:
        raise SpeechUnavailable("no speech synthesizer configured (set TBR_TTS_URL)")
    try:
        response = await net.request(
            "POST",
            settings.tts_url,
            json={
                "model": settings.tts_model,
                "input": text,
                "voice": settings.tts_voice,
                "response_format": "mp3",
            },
        )
    except Exception as exc:
        raise SpeechUnavailable(net.describe_error(exc)) from exc
    return response.content


_stt_model: Any = None
_stt_lock = asyncio.Lock()


async def transcribe(ctx: AppContext, audio_path: Path) -> str:
    """Transcribe a short recording on the CPU. The model loads on first use and stays resident."""
    if not ctx.settings.has_stt:
        raise SpeechUnavailable("speech recognition not installed (pip install 'tbr-shelf[voice]')")
    model = await _load_stt_model(ctx.settings.stt_model)

    def run() -> str:
        segments, _info = model.transcribe(str(audio_path), language="en", beam_size=1, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()

    return await asyncio.to_thread(run)


async def _load_stt_model(name: str) -> Any:
    global _stt_model
    async with _stt_lock:
        if _stt_model is None:
            from faster_whisper import WhisperModel

            _stt_model = await asyncio.to_thread(WhisperModel, name, device="cpu", compute_type="int8")
    return _stt_model
