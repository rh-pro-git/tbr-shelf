import re

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


def test_page_carries_live_search_and_the_tag_typeahead(client: TestClient) -> None:
    add_book(client, tags="fantasy, classics")
    page = client.get("/").text
    assert 'class="searchbar"' in page and 'form="filters" name="q"' in page
    assert '<input name="tag" list="taglist"' in page and '<option value="Classics">' in page
    assert re.search(r'data-tags="[^"]*Fantasy[^"]*"', page)
    assert '<p id="nomatch" class="empty" hidden>' in page


def test_detail_fragment_has_title_nav_rows_around_the_summary(
    client: TestClient, llm_client: TestClient
) -> None:
    plain = client.get(f"/api/books/{add_book(client)['id']}/detail").text
    assert plain.count('class="booknav"') == 1  # no model and no summary: only the row above
    with_model = llm_client.get(f"/api/books/{add_book(llm_client)['id']}/detail").text
    assert with_model.count('class="booknav"') == 2


def test_page_and_detail_carry_the_narrator_and_the_sample(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    client.patch(
        f"/api/books/{book['id']}",
        json={
            "version": book["version"],
            "narrator": "Rob Inglis",
            "store_url": "https://www.audible.com/pd/B0HOBBIT01",
        },
    )
    page = client.get("/").text
    assert 'data-narrator="Rob Inglis"' in page and 'id="beyond"' in page
    assert 'placeholder="Search title, author, narrator or tag"' in page
    fragment = client.get(f"/api/books/{book['id']}/detail").text
    assert "Narrated by Rob Inglis" in fragment and 'data-field="narrator"' in fragment
    assert f'data-a="sample" data-sample="/api/books/{book["id"]}/sample"' in fragment
    plain = client.get(f"/api/books/{add_book(client, title='Emma')['id']}/detail").text
    assert 'data-a="sample"' not in plain and "Narrated by" not in plain
