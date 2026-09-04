from fastapi.testclient import TestClient

from tests.conftest import add_book


def test_page_defers_the_detail_block(client: TestClient) -> None:
    add_book(client)
    page = client.get("/").text
    assert page.count('<div class="detail"><p class="lookup-note working">Loading…</p></div>') == 1
    assert 'data-field="notes"' not in page


def test_detail_fragment_is_one_block_with_the_actions_for_its_state(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    response = client.get(f"/api/books/{book['id']}/detail")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    fragment = response.text
    assert (
        fragment.lstrip().startswith('<div class="detail">') and fragment.count('<div class="detail">') == 1
    )
    assert 'data-field="notes"' in fragment and 'data-a="refresh"' in fragment
    assert 'data-a="archive"' in fragment and 'data-a="restore"' not in fragment
    assert client.get("/api/books/999999/detail").status_code == 404

    client.post(f"/api/books/{book['id']}/archive", json={"version": book["version"]})
    archived = client.get(f"/api/books/{book['id']}/detail").text
    assert 'data-a="restore"' in archived and 'data-a="delete"' in archived
    assert 'data-a="archive"' not in archived


def test_page_is_gzipped_when_the_client_accepts_it(client: TestClient) -> None:
    for title in ("Dune", "Emma", "Ulysses"):
        add_book(client, title=title)
    response = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert "Ulysses" in response.text
    image = client.get("/static/cover.svg", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in image.headers or image.headers["content-encoding"] != "gzip"


def test_static_assets_cache_by_version(client: TestClient) -> None:
    versioned = client.get("/static/app.js?v=1")
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"
    unversioned = client.get("/static/manifest.webmanifest")
    assert unversioned.headers["cache-control"] == "public, max-age=86400"
