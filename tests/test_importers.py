import pytest
from fastapi.testclient import TestClient

from tbr_shelf.importers import CsvFormatError, parse_csv
from tests.conftest import add_book


def test_parse_csv_is_header_case_insensitive_and_reports_bad_rows() -> None:
    rows, errors = parse_csv("Title,AUTHOR,shelf\nDune,Frank Herbert,audible\n,orphan,\n\n")
    assert rows == [
        {**{f: "" for f in rows[0]}, "title": "Dune", "author": "Frank Herbert", "shelf": "audible"}
    ]
    assert errors == ["Row 3: missing title, skipped"]


def test_parse_csv_requires_title_column() -> None:
    with pytest.raises(CsvFormatError):
        parse_csv("name,author\nDune,x\n")


def test_csv_import_endpoint_dedupes_against_library(client: TestClient) -> None:
    add_book(client, title="Dune", author="Frank Herbert", shelf="Audible")
    csv_text = (
        "title,author,shelf,status,tags,year\n"
        "Dune,Frank Herbert,Audible,Unread,,1965\n"
        "Emma,Jane Austen,Wishlist,Read,classics,1815\n"
    )
    response = client.post("/api/import/csv", files={"file": ("books.csv", csv_text, "text/csv")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"] == 1 and body["skipped_duplicates"] == 1
    emma = client.get("/?q=Emma").text
    assert "Classics" in emma


def test_csv_import_rejects_non_csv(client: TestClient) -> None:
    response = client.post("/api/import/csv", files={"file": ("books.txt", "title\nx", "text/plain")})
    assert response.status_code == 422


def test_enriched_import_dedupes_by_asin_then_title(client: TestClient) -> None:
    rows = [
        {
            "title": "Dune",
            "author": "Frank Herbert",
            "asin": "B0DUNE0001",
            "summary": "<p>Desert&nbsp;planet.</p>",
            "year": 1965,
        },
        {"title": "Dune (Unabridged)", "author": "Frank Herbert", "asin": "B0DUNE0001"},
        {"title": "Dune", "author": "Frank Herbert", "shelf": "Audible"},
        {"title": "Emma", "author": "Jane Austen", "shelf": "Wishlist", "started_at": "2026-01-02"},
    ]
    response = client.post("/api/import/books", json=rows)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"] == 2 and body["skipped_duplicates"] == 2
    dune = client.get(f"/api/books/{body['ids'][0]}").json()["book"]
    assert dune["store_url"] == "https://www.audible.com/pd/B0DUNE0001"
    assert dune["lookup_state"] == "ready" and dune["summary_state"] == "ready"
    emma = client.get(f"/api/books/{body['ids'][1]}").json()["book"]
    assert emma["started_at"] == "2026-01-02" and emma["summary_state"] == "none"


def test_enriched_import_validates_shape(client: TestClient) -> None:
    assert client.post("/api/import/books", json=[]).status_code == 422
    assert client.post("/api/import/books", json=[{"title": "x", "asin": "short"}]).status_code == 422
