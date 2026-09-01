from tbr_shelf.catalogs import match_exact, merge_candidates


def candidate(title: str, author: str, source: str, **extra) -> dict:
    return {"title": title, "author": author, "source": source, "editions": 0, **extra}


def test_merge_keeps_first_seen_and_fills_blanks_from_duplicates() -> None:
    merged = merge_candidates(
        [
            candidate("Dune", "Frank Herbert", "Open Library", year=1965, cover=""),
            candidate("dune", "frank herbert", "Audible", cover="https://c/1.jpg", asin="B000000001"),
            candidate("", "nobody", "Google Books"),
        ]
    )
    assert len(merged) == 1
    assert merged[0]["source"] == "Open Library"
    assert merged[0]["cover"] == "https://c/1.jpg"
    assert merged[0]["asin"] == "B000000001"
    assert merged[0]["year"] == 1965


def test_match_exact_prefers_author_overlap_then_edition_count() -> None:
    book = {"title": "Beloved", "author": "Toni Morrison"}
    candidates = [
        candidate("Beloved", "Study Guide Co", "Open Library", editions=400),
        candidate("Beloved", "Toni Morrison", "Open Library", editions=90),
        candidate("Beloved: A Novel", "Toni Morrison", "Audible"),
    ]
    assert match_exact(book, candidates)["author"] == "Toni Morrison"


def test_match_exact_returns_none_without_exact_title() -> None:
    assert match_exact({"title": "Beloved", "author": ""}, [candidate("Beloved: A Novel", "x", "s")]) is None


def test_match_exact_tie_breaks_by_editions_among_exact_titles_only() -> None:
    book = {"title": "Emma", "author": ""}
    best = match_exact(
        book,
        [candidate("Emma", "Jane Austen", "s", editions=5), candidate("Emma", "Someone", "s", editions=50)],
    )
    assert best["author"] == "Someone"
