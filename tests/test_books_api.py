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
