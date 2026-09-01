"""Hermetic fixtures: a temp data dir per test and no real network."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tbr_shelf import net
from tbr_shelf.app import create_app
from tbr_shelf.config import Settings
from tbr_shelf.context import AppContext


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(method: str, url: str, **_kwargs) -> httpx.Response:
        raise httpx.ConnectError(f"offline in tests: {method} {url}")

    monkeypatch.setattr(net, "request", refuse)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def llm_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data", llm_url="http://llm.test/v1/chat/completions", llm_model="test-model"
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, sweep_covers=False)) as test_client:
        yield test_client


@pytest.fixture
def llm_client(llm_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(llm_settings, sweep_covers=False)) as test_client:
        yield test_client


@pytest.fixture
def ctx(client: TestClient) -> AppContext:
    return client.app.state.ctx


def png_bytes(width: int = 120, height: int = 180, colour: tuple[int, int, int] = (180, 40, 40)) -> bytes:
    image = Image.new("RGB", (width, height), colour)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def add_book(client: TestClient, **fields) -> dict:
    payload = {"title": "The Hobbit", "author": "J. R. R. Tolkien", "shelf": "Audible", **fields}
    response = client.post("/api/books", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["book"]
