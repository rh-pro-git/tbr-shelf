import pytest
from fastapi.testclient import TestClient

from tbr_shelf import catalogs
from tests.conftest import add_book


def test_create_queues_lookup_and_records_offline_failure(client: TestClient) -> None:
    book = add_book(client)
    assert book["lookup_state"] in ("queued", "failed")
    current = client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["lookup_state"] == "failed"  # every catalog refused the connection
    assert current["summary_state"] == "none"  # no model configured, nothing was queued


def test_create_normalizes_tags_and_rejects_bad_status(client: TestClient) -> None:
    book = add_book(client, tags="fantasy; FANTASY, dragons")
    assert book["tags"] == "Fantasy, Dragons"
    assert client.post("/api/books", json={"title": "x", "status": "Burned"}).status_code == 422
    assert client.post("/api/books", json={"title": "", "shelf": "Audible"}).status_code == 422


def test_lookup_result_lands_on_the_book(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def canned(title: str, author: str = ""):
        return (
            [
                {
                    "title": title,
                    "author": author,
                    "year": 1937,
                    "cover": "",
                    "editions": 12,
                    "source": "Open Library",
                }
            ],
            {"Open Library": "ok", "Google Books": "ok", "Audible": "ok"},
        )

    monkeypatch.setattr(catalogs, "search_candidates", canned)
    book = add_book(client)
    current = client.get(f"/api/books/{book['id']}").json()["book"]
    assert current["lookup_state"] == "ready"
    assert current["year"] == 1937


def test_refresh_replaces_a_manual_edit_with_the_catalog_value(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def canned(title: str, author: str = ""):
        return (
            [
                {
                    "title": title,
                    "author": author,
                    "year": 1937,
                    "page_count": 310,
                    "cover": "",
                    "source": "OL",
                }
            ],
            {"Open Library": "ok", "Google Books": "ok", "Audible": "ok"},
        )

    monkeypatch.setattr(catalogs, "search_candidates", canned)
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    assert book["page_count"] == 310
    edited = client.patch(f"/api/books/{book['id']}", json={"version": book["version"], "page_count": 999})
    assert edited.json()["book"]["page_count"] == 999
    relooked = client.post(f"/api/books/{book['id']}/lookup", json={"version": book["version"] + 1})
    assert relooked.json()["message"] == "Lookup queued"
    assert (
        client.get(f"/api/books/{book['id']}").json()["book"]["page_count"] == 999
    )  # lookup fills blanks only
    current = client.get(f"/api/books/{book['id']}").json()["book"]
    refreshed = client.post(f"/api/books/{book['id']}/refresh", json={"version": current["version"]})
    assert refreshed.json()["message"] == "Refresh queued"
    after = client.get(f"/api/books/{book['id']}").json()["book"]
    assert after["page_count"] == 310 and after["lookup_state"] == "ready"


def test_stale_version_conflicts_with_current_version_in_detail(client: TestClient) -> None:
    book = add_book(client)
    current = client.get(f"/api/books/{book['id']}").json()["book"]
    ok = client.patch(f"/api/books/{book['id']}", json={"version": current["version"], "notes": "great"})
    assert ok.status_code == 200
    stale = client.patch(f"/api/books/{book['id']}", json={"version": current["version"], "notes": "again"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == current["version"] + 1


def test_status_changes_stamp_lifecycle_dates_once(client: TestClient) -> None:
    book = add_book(client)
    book = client.get(f"/api/books/{book['id']}").json()["book"]
    reading = client.patch(
        f"/api/books/{book['id']}", json={"version": book["version"], "status": "Reading"}
    ).json()["book"]
    assert reading["started_at"]
    finished = client.patch(
        f"/api/books/{book['id']}",
        json={"version": reading["version"], "status": "Finished", "finished_at": "2026-02-02"},
    ).json()["book"]
    assert finished["finished_at"] == "2026-02-02"
    assert finished["started_at"] == reading["started_at"]


def test_patch_validation(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    base = {"version": book["version"]}
    url = f"/api/books/{book['id']}"
    assert client.patch(url, json={**base, "started_at": "yesterday"}).status_code == 422
    assert client.patch(url, json={**base, "physical_format": "Scroll"}).status_code == 422
    assert client.patch(url, json={**base, "store_url": "http://insecure"}).status_code == 422
    assert client.patch(url, json={**base, "status": "Lost"}).status_code == 422
    assert client.patch(url, json={**base, "spine_pref": "codex"}).status_code == 422
    cleared = client.patch(url, json={**base, "physical_format": "", "store_url": ""}).json()["book"]
    assert cleared["physical_format"] is None and cleared["store_url"] is None


def test_delete_requires_an_archived_current_book(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    url = f"/api/books/{book['id']}"
    assert client.request("DELETE", url, json={"version": book["version"]}).status_code == 409
    archived = client.post(f"{url}/archive", json={"version": book["version"]}).json()["book"]
    assert archived["status"] == "Archived" and archived["archived"] == 1
    assert client.request("DELETE", url, json={"version": archived["version"]}).status_code == 200
    assert client.get(url).status_code == 404


def test_restore_returns_to_unread(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    archived = client.post(f"/api/books/{book['id']}/archive", json={"version": book["version"]}).json()[
        "book"
    ]
    restored = client.post(f"/api/books/{book['id']}/restore", json={"version": archived["version"]}).json()[
        "book"
    ]
    assert restored["status"] == "Unread" and restored["archived"] == 0


def test_index_filters_and_counts(client: TestClient) -> None:
    add_book(client, title="Dune", shelf="Audible", tags="sci-fi")
    add_book(client, title="Emma", author="Jane Austen", shelf="Wishlist", tags="classics")
    page = client.get("/?shelf=Wishlist").text
    assert "Emma" in page and "Dune" not in page
    assert "Audible (1)" in page and "Wishlist (1)" in page
    tagged = client.get("/?tag=classics").text
    assert "Emma" in tagged and "Dune" not in tagged
    searched = client.get("/?q=dun").text
    assert "Dune" in searched and "Emma" not in searched


def test_ai_actions_are_unavailable_without_a_model(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    assert (
        client.post(f"/api/books/{book['id']}/summary", json={"version": book["version"]}).status_code == 503
    )
    assert (
        client.post(f"/api/books/{book['id']}/similar", json={"version": book["version"]}).status_code == 503
    )
    page = client.get("/").text
    assert "Similar books" not in page
    assert 'id="vc-fab"' not in page


def test_health_reports_schema_and_features(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["ok"] and body["schema_version"] >= 7
    assert body["features"] == {"llm": False, "tts": False, "voice": False}


def test_tbr_is_a_status_and_a_virtual_shelf(client: TestClient) -> None:
    dune = add_book(client, title="Dune", shelf="Audible")
    add_book(client, title="Emma", author="Jane Austen", shelf="Wishlist")
    dune = client.get(f"/api/books/{dune['id']}").json()["book"]  # the lookup task bumped the version
    patched = client.patch(f"/api/books/{dune['id']}", json={"version": dune["version"], "status": "TBR"})
    assert patched.status_code == 200 and patched.json()["book"]["status"] == "TBR"
    page = client.get("/?shelf=TBR").text
    assert "Dune" in page and "Emma" not in page
    assert "TBR (1)" in page and 'class="on" href="/?shelf=TBR"' in page
    assert "Dune" in client.get("/?shelf=Audible").text  # the real shelf is untouched
    assert (
        client.get("/?shelf=TBR&status=Reading").status_code == 200
    )  # the tab wins over a stale status filter


PRODUCT = {
    "title": "Dune", "author": "Frank Herbert", "narrator": "Scott Brick", "series_title": "Dune Saga",
    "year": 2007, "cover": "https://img/dune.jpg", "audible_cover": "https://img/dune.jpg",
    "asin": "B0DUNE0001", "minutes": 1265, "abridged": False, "source": "Audible", "editions": 0,
    "page_count": None, "sample_url": "https://samples/dune.mp3", "summary": "Desert planet.",
}  # fmt: skip


def stub_product(monkeypatch: pytest.MonkeyPatch, product: dict | None) -> None:
    async def fake(_asin: str):
        return product

    monkeypatch.setattr(catalogs, "audible_product", fake)


def test_add_by_asin_lands_enriched_and_runs_no_lookup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_product(monkeypatch, PRODUCT)
    response = client.post("/api/books", json={"title": "dune", "asin": "B0DUNE0001", "shelf": "Wishlist"})
    assert response.status_code == 201, response.text
    book = client.get(f"/api/books/{response.json()['book']['id']}").json()["book"]
    assert book["title"] == "Dune" and book["narrator"] == "Scott Brick" and book["series"] == "Dune Saga"
    assert book["store_url"] == "https://www.audible.com/pd/B0DUNE0001"
    assert book["audiobook_length"] == "21h 5m" and book["year"] == 2007
    assert book["cover"] == "https://img/dune.jpg" and book["lookup_state"] == "ready"
    assert book["summary_state"] == "ready" and book["summary"] == "Desert planet."
    again = client.post("/api/books", json={"title": "Dune", "asin": "B0DUNE0001", "shelf": "Wishlist"})
    assert again.status_code == 409 and again.json()["detail"]["book_id"] == book["id"]
    stub_product(monkeypatch, {**PRODUCT, "asin": "B0DUNE0002", "title": "Dune Messiah", "summary": ""})
    silent = client.post("/api/books", json={"title": "x", "asin": "B0DUNE0002"}).json()["book"]
    assert silent["summary_state"] == "none"  # no model configured, so nothing is queued for it


def test_add_by_asin_rejects_what_audible_cannot_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        client.post("/api/books", json={"title": "x", "asin": "B0OFFLINE1"}).status_code == 503
    )  # catalog refused
    stub_product(monkeypatch, None)
    assert client.post("/api/books", json={"title": "x", "asin": "B000000000"}).status_code == 404
    assert client.post("/api/books", json={"title": "x", "asin": "nope"}).status_code == 422


def test_search_tags_hits_already_in_the_library(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    owned = client.get(f"/api/books/{add_book(client, title='Dune', author='Frank Herbert')['id']}").json()[
        "book"
    ]
    client.patch(
        f"/api/books/{owned['id']}",
        json={"version": owned["version"], "store_url": "https://www.audible.com/pd/B0DUNE0001"},
    )
    emma = add_book(client, title="Emma", author="Jane Austen", shelf="Wishlist")

    async def fake(keywords: str):
        assert keywords == "Frank Herbert"
        return [
            {"title": "Dune Messiah", "author": "Frank Herbert", "asin": "B0DUNE0002"},
            {"title": "Dune", "author": "Frank Herbert", "asin": "B0DUNE0001"},
            {"title": "Emma", "author": "Jane Austen", "asin": "B0EMMA0001"},
        ]

    monkeypatch.setattr(catalogs, "audible_search", fake)
    results = client.get("/api/search", params={"q": "  Frank   Herbert "}).json()["results"]
    assert [hit["book_id"] for hit in results] == [None, owned["id"], emma["id"]]
    assert client.get("/api/search", params={"q": "x"}).json() == {"q": "x", "results": []}


def test_search_reports_an_unreachable_catalog(client: TestClient) -> None:
    assert client.get("/api/search", params={"q": "Dune"}).status_code == 503


def test_sample_redirects_to_audible(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    assert client.get(f"/api/books/{book['id']}/sample").status_code == 404  # no Audible edition on it
    client.patch(
        f"/api/books/{book['id']}",
        json={"version": book["version"], "store_url": "https://www.audible.com/pd/B0HOBBIT01"},
    )
    stub_product(monkeypatch, {**PRODUCT, "asin": "B0HOBBIT01", "sample_url": "https://samples/hobbit.mp3"})
    response = client.get(f"/api/books/{book['id']}/sample", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "https://samples/hobbit.mp3"
    stub_product(monkeypatch, {**PRODUCT, "asin": "B0HOBBIT01", "sample_url": None})
    assert client.get(f"/api/books/{book['id']}/sample", follow_redirects=False).status_code == 404


def test_narrator_is_editable_and_searchable(client: TestClient) -> None:
    book = client.get(f"/api/books/{add_book(client)['id']}").json()["book"]
    patched = client.patch(
        f"/api/books/{book['id']}", json={"version": book["version"], "narrator": "  Rob Inglis "}
    )
    assert patched.json()["book"]["narrator"] == "Rob Inglis"
    assert "The Hobbit" in client.get("/?q=Inglis").text
    cleared = client.patch(f"/api/books/{book['id']}", json={"version": book["version"] + 1, "narrator": ""})
    assert cleared.json()["book"]["narrator"] is None
