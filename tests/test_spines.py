import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from tbr_shelf.context import AppContext
from tbr_shelf.covers import cover_path
from tbr_shelf.spines import RENDER_SCALE, effective_spine_source, render_spine
from tests.conftest import add_book, png_bytes


def test_render_spine_produces_webp_at_render_scale() -> None:
    rendered = render_spine(png_bytes(), "A Very Long Title That Needs Wrapping", "Some Author", 48, 190)
    image = Image.open(io.BytesIO(rendered))
    assert image.format == "WEBP"
    assert image.size == (48 * RENDER_SCALE, 190 * RENDER_SCALE)


def test_effective_source_follows_preference_then_readiness(ctx: AppContext) -> None:
    book = {"id": 1, "spine_pref": "auto", "spine_state": "ready"}
    assert effective_spine_source(ctx, book) == "local"  # no asset on disk
    ctx.settings.external_spines_dir.mkdir(parents=True)
    (ctx.settings.external_spines_dir / "1.png").write_bytes(png_bytes(10, 40))
    assert effective_spine_source(ctx, book) == "external"
    assert effective_spine_source(ctx, {**book, "spine_state": "failed"}) == "local"
    assert (
        effective_spine_source(ctx, {**book, "spine_state": "failed", "spine_pref": "external"}) == "external"
    )
    assert effective_spine_source(ctx, {**book, "spine_pref": "local"}) == "local"


def test_spine_endpoint_needs_a_cached_cover(client: TestClient, ctx: AppContext) -> None:
    book = add_book(client)
    assert client.get(f"/api/books/{book['id']}/spine").status_code == 404
    ctx.settings.covers_dir.mkdir(parents=True)
    cover_path(ctx, book["id"]).write_bytes(png_bytes())
    response = client.get(f"/api/books/{book['id']}/spine?w=40&h=160")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(response.content)).size == (40 * RENDER_SCALE, 160 * RENDER_SCALE)
    assert client.get(f"/api/books/{book['id']}/spine?w=1&h=9999").status_code == 200  # clamped, not rejected


def test_external_spine_asset_round_trip(client: TestClient) -> None:
    book = add_book(client)
    meta = {"height_in": 9.5, "thickness_in": 1.2, "source_type": "photo-informed reconstruction"}
    response = client.put(
        f"/api/books/{book['id']}/spine-asset",
        data={"meta": json.dumps(meta)},
        files={"file": ("spine.png", png_bytes(120, 950), "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["effective"] == "external"
    served = client.get(f"/api/books/{book['id']}/spine?w=30&h=240")
    assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(served.content)).size == (30 * RENDER_SCALE, 240 * RENDER_SCALE)
    detail = client.get(f"/api/books/{book['id']}/detail").text
    assert "photo-informed" in detail and "9.5 × 1.2 in" in detail  # noqa: RUF001
    queue = client.get("/api/spine-queue").json()["books"]
    assert queue == []


def test_external_spine_fits_keep_every_requested_size(client: TestClient, ctx: AppContext) -> None:
    book = add_book(client)
    client.put(
        f"/api/books/{book['id']}/spine-asset",
        data={"meta": json.dumps({"height_in": 9.0, "thickness_in": 1.0})},
        files={"file": ("spine.png", png_bytes(120, 950), "image/png")},
    )
    for size in ("w=30&h=240", "w=40&h=300", "w=30&h=240"):
        assert client.get(f"/api/books/{book['id']}/spine?{size}").status_code == 200
    fits = sorted(path.name for path in ctx.settings.spines_dir.glob(f"{book['id']}-external-*"))
    assert [name.split("-")[2] for name in fits] == ["30x240", "40x300"]
    assert all(name.endswith(".webp") for name in fits)
    assert not list(ctx.settings.spines_dir.glob("*.tmp"))


def test_external_spine_asset_validation(client: TestClient) -> None:
    book = add_book(client)
    url = f"/api/books/{book['id']}/spine-asset"
    assert client.put(url, data={"meta": "[]"}).status_code == 422
    assert client.put(url, data={"meta": '{"height_in": "tall"}'}).status_code == 422
    not_png = client.put(url, data={"meta": "{}"}, files={"file": ("s.png", b"GIF89a....", "image/png")})
    assert not_png.status_code == 422
    failed = client.put(url, data={"meta": json.dumps({"notes": "no edition found"})})
    assert failed.json()["book"]["spine_state"] == "failed"


def test_regen_request_appears_in_queue(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    client.post(f"/api/books/{book['id']}/spine-regen", json={"version": book["version"]})
    queue = client.get("/api/spine-queue").json()["books"]
    assert [entry["id"] for entry in queue] == [book["id"]]
