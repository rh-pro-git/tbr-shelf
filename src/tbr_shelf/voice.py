"""The voice librarian: a books-only assistant grounded in the live library.

Conversation history lives in memory per browser session and dies on restart by
design. Turns are also persisted so an external reviewer can distill them into
preferences; those preferences only reach the prompt after a human writes them
back through the preferences endpoint. The assistant's memory of the reader is
gated on that review.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from . import llm
from .books import library_rows_for_prompt
from .config import Settings
from .context import VOICE_SESSION_CAP, AppContext
from .db import Database, utc_now

MAX_TURNS = 12
IDLE_MINUTES = 30
MAX_PREFERENCE_LINES = 40
NOTE_PREVIEW_CHARS = 120

SYSTEM_PROMPT = (
    "You are the voice assistant inside the user's personal book tracker. You ONLY discuss books and "
    "reading: their library below, what to read next from the TBR (status Unread), comparisons, "
    "similar-book ideas, series order, reading plans. If asked about anything unrelated to books, "
    "decline in one short sentence and steer back to reading. Your reply is spoken aloud: 2-4 "
    "conversational sentences, no lists, no markdown, no stage directions. Never claim a book is in "
    "their library unless it appears below; frame outside titles clearly as suggestions."
)


def library_context(db: Database) -> str:
    lines = []
    for row in library_rows_for_prompt(db):
        parts = [f"{row['title']} by {row['author'] or 'unknown'}"]
        if row["series"]:
            parts.append(f"series: {row['series']}")
        parts.append(f"shelf: {row['shelf']}")
        parts.append(f"status: {row['status']}")
        if row["tags"]:
            parts.append(f"tags: {row['tags']}")
        if row["year"]:
            parts.append(str(row["year"]))
        if row["finished_at"]:
            parts.append(f"finished {row['finished_at']}")
        elif row["started_at"]:
            parts.append(f"started {row['started_at']}")
        if row["notes"]:
            parts.append("notes: " + " ".join(row["notes"].split())[:NOTE_PREVIEW_CHARS])
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines) or "(library is empty)"


def read_preferences(settings: Settings) -> list[str]:
    try:
        data = json.loads(settings.preferences_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [str(line) for line in (data.get("lines") or [])][:MAX_PREFERENCE_LINES]


def save_preferences(settings: Settings, lines: list[str]) -> int:
    kept = [str(line)[:300] for line in lines[:100]]
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.preferences_path.write_text(json.dumps({"lines": kept, "updated_at": utc_now()}))
    return len(kept)


def preferences_block(settings: Settings) -> str:
    lines = read_preferences(settings)
    if not lines:
        return ""
    return "\n\nOperator-reviewed reading preferences learned from past conversations:\n" + "\n".join(
        f"- {line}" for line in lines
    )


def _idle_cutoff() -> str:
    return (datetime.now(UTC) - timedelta(minutes=IDLE_MINUTES)).replace(microsecond=0).isoformat()


def open_conversation(db: Database, sid: str) -> int:
    """The open conversation for this session if it was active recently, else a new one."""
    stamp = utc_now()
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT id FROM voice_conversations WHERE sid=? AND closed=0 AND last_at>=? "
            "ORDER BY id DESC LIMIT 1",
            (sid, _idle_cutoff()),
        ).fetchone()
        if row:
            connection.execute("UPDATE voice_conversations SET last_at=? WHERE id=?", (stamp, row[0]))
            return int(row[0])
        cursor = connection.execute(
            "INSERT INTO voice_conversations(sid, started_at, last_at) VALUES(?, ?, ?)", (sid, stamp, stamp)
        )
        return int(cursor.lastrowid)


def record_turns(db: Database, conversation_id: int, user_text: str, reply: str) -> None:
    stamp = utc_now()
    with db.transaction() as connection:
        connection.executemany(
            "INSERT INTO voice_turns(conv_id, role, content, created_at) VALUES(?, ?, ?, ?)",
            [(conversation_id, "user", user_text, stamp), (conversation_id, "assistant", reply, stamp)],
        )


def close_conversations(db: Database, sid: str) -> None:
    with db.transaction() as connection:
        connection.execute("UPDATE voice_conversations SET closed=1 WHERE sid=? AND closed=0", (sid,))


def exportable_conversations(db: Database) -> list[dict]:
    """Conversations a reviewer may distill: closed or idle, and not yet marked ingested."""
    with db.transaction() as connection:
        conversations = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM voice_conversations WHERE ingested=0 AND (closed=1 OR last_at<?) ORDER BY id",
                (_idle_cutoff(),),
            ).fetchall()
        ]
        for conversation in conversations:
            conversation["turns"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT role, content, created_at FROM voice_turns WHERE conv_id=? ORDER BY id",
                    (conversation["id"],),
                ).fetchall()
            ]
    return conversations


def mark_ingested(db: Database, conversation_id: int) -> bool:
    with db.transaction() as connection:
        cursor = connection.execute(
            "UPDATE voice_conversations SET ingested=1, closed=1 WHERE id=?", (conversation_id,)
        )
        return cursor.rowcount > 0


def session_key(sid: str) -> str:
    return sid[:64]


def remember_exchange(ctx: AppContext, sid: str, user_text: str, reply: str) -> None:
    history = ctx.voice_sessions.setdefault(session_key(sid), [])
    history.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}])
    del history[: -2 * MAX_TURNS]
    while len(ctx.voice_sessions) > VOICE_SESSION_CAP:
        ctx.voice_sessions.pop(next(iter(ctx.voice_sessions)))


def forget_session(ctx: AppContext, sid: str) -> None:
    ctx.voice_sessions.pop(session_key(sid), None)


async def answer(ctx: AppContext, sid: str, user_text: str) -> str:
    system = (
        SYSTEM_PROMPT
        + preferences_block(ctx.settings)
        + "\n\nTheir library right now:\n"
        + library_context(ctx.db)
    )
    history = ctx.voice_sessions.get(session_key(sid), [])
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": user_text}]
    reply = await llm.chat(ctx, messages, temperature=0.6, max_tokens=400)
    if not reply:
        raise llm.LLMUnavailable("empty reply")
    remember_exchange(ctx, sid, user_text, reply)
    record_turns(ctx.db, open_conversation(ctx.db, session_key(sid)), user_text, reply)
    return reply
