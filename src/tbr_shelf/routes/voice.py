"""Voice chat and the operator-gated learning loop around it."""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import voice
from ..context import AppContext
from ..models import PreferencesUpdate
from ..speech import SpeechUnavailable, synthesize, transcribe
from . import get_ctx

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice")

AUDIO_MAX_BYTES = 10_000_000


@router.post("/chat")
async def chat(
    sid: str = Form(...), audio: UploadFile = File(...), ctx: AppContext = Depends(get_ctx)
) -> dict:
    if not ctx.settings.has_voice_chat:
        raise HTTPException(503, "Voice chat needs a language model and speech recognition. See the README.")
    raw = await audio.read()
    if not raw or len(raw) > AUDIO_MAX_BYTES:
        raise HTTPException(422, "Audio missing or too large")
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(raw)
        recording = Path(handle.name)
    try:
        text = await transcribe(ctx, recording)
    except Exception as exc:
        raise HTTPException(422, "Couldn't process that audio. Try again.") from exc
    finally:
        recording.unlink(missing_ok=True)
    if not text:
        raise HTTPException(422, "Couldn't hear that. Try again closer to the mic.")

    reply = await voice.answer(ctx, sid, text)

    audio_b64 = ""
    if ctx.settings.has_tts:
        try:
            audio_b64 = base64.b64encode(await synthesize(ctx, reply)).decode()
        except SpeechUnavailable as exc:
            log.info("reply audio skipped: %s", exc)
    return {"transcript": text, "reply": reply, "audio_b64": audio_b64}


@router.delete("/chat/{sid}")
async def reset(sid: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    voice.forget_session(ctx, sid)
    voice.close_conversations(ctx.db, voice.session_key(sid))
    return {"reset": True}


@router.get("/conversations")
async def conversations(ctx: AppContext = Depends(get_ctx)) -> dict:
    """For an external reviewer: closed or idle conversations not yet marked ingested, turns inline."""
    return {"conversations": voice.exportable_conversations(ctx.db)}


@router.post("/conversations/{conversation_id}/ingested")
async def ingested(conversation_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    if not voice.mark_ingested(ctx.db, conversation_id):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


@router.put("/preferences")
async def put_preferences(payload: PreferencesUpdate, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"ok": True, "count": voice.save_preferences(ctx.settings, payload.lines)}
