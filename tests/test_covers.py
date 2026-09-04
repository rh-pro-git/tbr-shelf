import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tbr_shelf import net
from tbr_shelf.context import AppContext
from tbr_shelf.covers import cache_cover, cover_path, fit_cover
from tests.conftest import add_book, png_bytes


def cache(ctx: AppContext, book_id: int, data: bytes) -> Path:
    ctx.settings.covers_dir.mkdir(parents=True, exist_ok=True)
    path = cover_path(ctx, book_id)
    path.write_bytes(data)
    return path


def test_bare_cover_serves_the_source_bytes(client: TestClient, ctx: AppContext) -> None:
    book = add_book(client)
    data = png_bytes(500, 500)
    cache(ctx, book["id"], data)
    response = client.get(f"/api/books/{book['id']}/cover")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.content == data


def test_width_serves_a_webp_derivative_and_keeps_sizes_apart(client: TestClient, ctx: AppContext) -> None:
    book = add_book(client)
    cache(ctx, book["id"], png_bytes(500, 500))
    response = client.get(f"/api/books/{book['id']}/cover?w=200")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "WEBP" and image.size == (200, 200)
    clamped = Image.open(io.BytesIO(client.get(f"/api/books/{book['id']}/cover?w=9999").content))
    assert clamped.size == (500, 500)  # width clamps to 640 and a thumbnail never upscales
    fits = sorted(path.name for path in ctx.settings.cover_fits_dir.glob(f"{book['id']}-*.webp"))
    assert [name.split("-")[1] for name in fits] == ["200", "640"]
    assert not list(ctx.settings.cover_fits_dir.glob("*.tmp"))


def test_width_on_an_uncached_cover_still_redirects(client: TestClient) -> None:
    book = add_book(client)
    response = client.get(f"/api/books/{book['id']}/cover?w=200", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/static/cover.svg")


class Canned:
    def __init__(self, content: bytes) -> None:
        self.content = content


async def test_cache_cover_swaps_atomically_and_purges_derivatives(
    client: TestClient, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = add_book(client)
    current = client.get(f"/api/books/{book['id']}").json()["book"]
    client.patch(
        f"/api/books/{book['id']}", json={"version": current["version"], "cover": "https://c.test/a.png"}
    )
    old = cache(ctx, book["id"], png_bytes(300, 300, (10, 10, 200)))
    derivative = fit_cover(ctx, book["id"], 200)
    assert derivative.exists()

    new = png_bytes(300, 300, (200, 10, 10))

    async def deliver(method: str, url: str, **_kwargs) -> Canned:
        return Canned(new)

    monkeypatch.setattr(net, "request", deliver)
    assert await cache_cover(ctx, book["id"]) is True
    assert old.read_bytes() == new
    assert not derivative.exists()
    assert not list(ctx.settings.covers_dir.glob("*.tmp"))
    assert oct(old.stat().st_mode)[-3:] == "664"

    derivative = fit_cover(ctx, book["id"], 200)

    async def fail(method: str, url: str, **_kwargs) -> Canned:
        raise ConnectionError("simulated download failure")

    monkeypatch.setattr(net, "request", fail)
    assert await cache_cover(ctx, book["id"]) is False
    assert old.read_bytes() == new
    assert derivative.exists()
