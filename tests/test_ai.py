import base64

import pytest
from fastapi.testclient import TestClient

from tbr_shelf import catalogs, llm, speech, summaries, voice
from tbr_shelf.context import AppContext
from tbr_shelf.summaries import parse_suggestions
from tests.conftest import add_book


def stub_chat(monkeypatch: pytest.MonkeyPatch, reply: str | Exception) -> list[list[dict]]:
    seen: list[list[dict]] = []

    async def fake(_ctx, messages, **_kwargs) -> str:
        seen.append(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm, "chat", fake)
    return seen


def test_parse_suggestions_tolerates_prose_around_json() -> None:
    text = 'Sure! [{"title": "A", "author": "B"}, {"title": "", "author": "x"}, "junk", {"title": "C"}]'
    assert parse_suggestions(text) == [{"title": "A", "author": "B"}, {"title": "C", "author": ""}]


def test_summary_states(llm_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_chat(monkeypatch, "A hobbit goes on a journey.")
    book = add_book(llm_client)
    current = llm_client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["summary_state"] == "ready" and current["summary"].startswith("A hobbit")

    stub_chat(monkeypatch, summaries.UNAVAILABLE_TOKEN)
    llm_client.post(f"/api/books/{book['id']}/summary", json={"version": current["version"]})
    current = llm_client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["summary_state"] == "unavailable" and current["summary"] == ""

    stub_chat(monkeypatch, llm.LLMUnavailable("timeout"))
    llm_client.post(f"/api/books/{book['id']}/summary", json={"version": current["version"]})
    assert llm_client.get(f"/api/books/{book['id']}").json()["book"]["summary_state"] == "failed"


def test_similar_books_endpoint(llm_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_chat(monkeypatch, '[{"title": "Dune", "author": "Frank Herbert"}]')
    book = llm_client.get(f"/api/books/{add_book(llm_client)['id']}").json()["book"]
    response = llm_client.post(f"/api/books/{book['id']}/similar", json={"version": book["version"]})
    assert response.json() == {"suggestions": [{"title": "Dune", "author": "Frank Herbert"}]}
    assert llm_client.post(f"/api/books/{book['id']}/similar", json={"version": 999}).status_code == 409
    stub_chat(monkeypatch, "not json at all")
    assert (
        llm_client.post(f"/api/books/{book['id']}/similar", json={"version": book["version"]}).status_code
        == 503
    )


def test_voice_answer_is_grounded_in_the_library_and_gated_preferences(
    llm_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx: AppContext = llm_client.app.state.ctx
    add_book(llm_client, title="Dune", author="Frank Herbert", notes="loved the worldbuilding")
    llm_client.put("/api/voice/preferences", json={"lines": ["prefers long epics"]})
    seen = stub_chat(monkeypatch, "Try Dune next.")

    import asyncio

    reply = asyncio.run(voice.answer(ctx, "sid-1", "what should I read?"))
    assert reply == "Try Dune next."
    system_prompt = seen[0][0]["content"]
    assert "Dune by Frank Herbert" in system_prompt
    assert "notes: loved the worldbuilding" in system_prompt
    assert "prefers long epics" in system_prompt
    assert ctx.voice_sessions["sid-1"][-1] == {"role": "assistant", "content": "Try Dune next."}

    # A second turn carries history; the exchange is also persisted for review.
    asyncio.run(voice.answer(ctx, "sid-1", "why?"))
    assert [m["role"] for m in seen[1]] == ["system", "user", "assistant", "user"]
    llm_client.delete("/api/voice/chat/sid-1")
    exported = llm_client.get("/api/voice/conversations").json()["conversations"]
    assert len(exported) == 1 and len(exported[0]["turns"]) == 4
    assert llm_client.post(f"/api/voice/conversations/{exported[0]['id']}/ingested").status_code == 200
    assert llm_client.get("/api/voice/conversations").json()["conversations"] == []
    assert llm_client.post("/api/voice/conversations/999/ingested").status_code == 404


def test_voice_history_is_capped(llm_client: TestClient) -> None:
    ctx: AppContext = llm_client.app.state.ctx
    for turn in range(voice.MAX_TURNS + 5):
        voice.remember_exchange(ctx, "sid", f"q{turn}", f"a{turn}")
    assert len(ctx.voice_sessions["sid"]) == 2 * voice.MAX_TURNS
    assert ctx.voice_sessions["sid"][0]["content"] == "q5"


def test_voice_chat_requires_a_model_and_speech_recognition(client: TestClient) -> None:
    response = client.post(
        "/api/voice/chat", data={"sid": "s"}, files={"audio": ("a.webm", b"x" * 2000, "audio/webm")}
    )
    assert response.status_code == 503


def test_voice_chat_pipeline_with_stubbed_speech(
    llm_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_transcribe(_ctx, _path) -> str:
        return "what next"

    async def fake_synthesize(_ctx, _text) -> bytes:
        return b"MP3DATA"

    monkeypatch.setattr(type(llm_client.app.state.ctx.settings), "has_stt", property(lambda _self: True))
    monkeypatch.setattr(type(llm_client.app.state.ctx.settings), "has_tts", property(lambda _self: True))
    monkeypatch.setattr("tbr_shelf.routes.voice.transcribe", fake_transcribe)
    monkeypatch.setattr("tbr_shelf.routes.voice.synthesize", fake_synthesize)
    stub_chat(monkeypatch, "Read Dune.")
    response = llm_client.post(
        "/api/voice/chat", data={"sid": "s"}, files={"audio": ("a.webm", b"x" * 2000, "audio/webm")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcript"] == "what next" and body["reply"] == "Read Dune."
    assert base64.b64decode(body["audio_b64"]) == b"MP3DATA"


def test_summary_audio_without_tts_is_503(llm_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_chat(monkeypatch, "A summary.")
    book = add_book(llm_client)
    response = llm_client.get(f"/api/books/{book['id']}/audio")
    assert response.status_code == 503
    assert isinstance(speech.SpeechUnavailable("x"), RuntimeError)


def test_summary_prefers_the_publisher_copy_when_the_book_has_an_asin(
    llm_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = stub_chat(monkeypatch, "A model-written summary.")
    book = add_book(llm_client)
    current = llm_client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["summary"] == "A model-written summary." and len(seen) == 1

    async def publisher(asin: str) -> str:
        assert asin == "B000000001"
        return "Publisher copy."

    monkeypatch.setattr(catalogs, "publisher_summary", publisher)
    current = llm_client.patch(
        f"/api/books/{book['id']}",
        json={"version": current["version"], "store_url": "https://www.audible.com/pd/B000000001"},
    ).json()["book"]
    llm_client.post(f"/api/books/{book['id']}/summary", json={"version": current["version"]})
    current = llm_client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["summary_state"] == "ready" and current["summary"] == "Publisher copy."
    assert len(seen) == 1  # the model was not consulted


def test_summary_falls_back_to_the_model_when_the_catalog_has_no_copy(
    llm_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_chat(monkeypatch, "From the model.")
    book = add_book(llm_client)
    current = llm_client.get(f"/api/books/{book['id']}").json()["book"]
    current = llm_client.patch(
        f"/api/books/{book['id']}",
        json={"version": current["version"], "store_url": "https://www.audible.com/pd/B000000002"},
    ).json()["book"]
    # the offline fixture fails the catalog request, which must read as "no copy", not as an error
    llm_client.post(f"/api/books/{book['id']}/summary", json={"version": current["version"]})
    current = llm_client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["summary_state"] == "ready" and current["summary"] == "From the model."


def test_a_padded_unavailable_reply_is_unavailable(
    llm_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_chat(monkeypatch, f"{summaries.UNAVAILABLE_TOKEN}\n\nAs of now there is no record of such a book.")
    book = add_book(llm_client)
    current = llm_client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["summary_state"] == "unavailable" and current["summary"] == ""
