from tbr_shelf.catalogs import audible_candidate, match_exact, merge_candidates


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


def test_merge_carries_narrator_and_series_title_across_sources() -> None:
    merged = merge_candidates(
        [
            candidate("Dune", "Frank Herbert", "Open Library", year=1965, cover=""),
            candidate(
                "Dune",
                "Frank Herbert",
                "Audible",
                asin="B000000001",
                narrator="Scott Brick",
                series_title="Dune Saga",
            ),
        ]
    )
    assert len(merged) == 1 and merged[0]["source"] == "Open Library"
    assert merged[0]["narrator"] == "Scott Brick"
    assert merged[0]["series_title"] == "Dune Saga"


def test_audible_candidate_reads_one_catalog_product() -> None:
    product = {
        "asin": "B0DUNE0001", "title": "Dune", "language": "English", "release_date": "2007-01-05",
        "authors": [{"name": "Frank Herbert"}],
        "narrators": [{"name": "Scott Brick"}, {"name": "Orlagh Cassidy"}, {}],
        "series": [{"title": "Dune Saga", "sequence": "1"}], "runtime_length_min": 1265,
        "product_images": {"500": "https://img/500.jpg"}, "format_type": "unabridged",
        "sample_url": "https://samples/x.mp3",
    }  # fmt: skip
    parsed = audible_candidate(product)
    assert parsed["narrator"] == "Scott Brick, Orlagh Cassidy"
    assert parsed["series_title"] == "Dune Saga" and parsed["year"] == 2007 and parsed["minutes"] == 1265
    assert parsed["cover"] == parsed["audible_cover"] == "https://img/500.jpg"
    assert parsed["sample_url"] == "https://samples/x.mp3" and parsed["abridged"] is False
    assert audible_candidate({**product, "language": "spanish"}) is None
    assert audible_candidate({"asin": "B000000000", "is_vvab": False}) is None  # what a bad ASIN returns
