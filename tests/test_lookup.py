from tbr_shelf.lookup import candidate_changes, lookup_changes, parse_lookup_envelope

OK = {"Open Library": "ok", "Google Books": "rate-limited", "Audible": "ok"}


def book(**fields) -> dict:
    return {
        "id": 1, "title": "Dune", "author": "Frank Herbert", "shelf": "Audible", "year": None,
        "page_count": None, "physical_format": None, "cover": "", "store_url": None,
        "audiobook_length": "", "version": 1, **fields,
    }  # fmt: skip


def test_exact_match_fills_blanks_and_marks_ready() -> None:
    candidates = [
        {
            "title": "Dune", "author": "Frank Herbert", "year": 1965, "page_count": 412,
            "physical_format": "Paperback", "cover": "https://ol/cover.jpg", "editions": 30,
            "source": "Open Library",
        },
    ]  # fmt: skip
    changes = lookup_changes(book(), candidates, OK)
    assert changes["lookup_state"] == "ready"
    assert changes["year"] == 1965
    assert changes["page_count"] == 412
    assert changes["cover"] == "https://ol/cover.jpg"


def test_audible_match_supplies_store_link_runtime_and_default_cover() -> None:
    candidates = [
        {
            "title": "Dune", "author": "Frank Herbert", "year": 1965, "cover": "https://ol/cover.jpg",
            "editions": 30, "source": "Open Library", "asin": "B0DUNE0001", "minutes": 1265,
            "audible_cover": "https://aud/cover.jpg",
        },
    ]  # fmt: skip
    changes = lookup_changes(book(), candidates, OK)
    assert changes["store_url"] == "https://www.audible.com/pd/B0DUNE0001"
    assert changes["audiobook_length"] == "21h 5m"
    assert changes["cover"] == "https://aud/cover.jpg"


def test_existing_values_are_never_overwritten() -> None:
    candidates = [
        {
            "title": "Dune",
            "author": "Frank Herbert",
            "year": 1965,
            "cover": "https://new",
            "editions": 1,
            "source": "s",
        }
    ]
    changes = lookup_changes(book(year=2000, cover="https://mine"), candidates, OK)
    assert "year" not in changes
    assert "cover" not in changes


def test_refresh_replaces_catalog_fields_but_not_the_author_or_cover() -> None:
    candidates = [
        {
            "title": "Dune", "author": "Frank Herbert", "year": 1965, "page_count": 412,
            "physical_format": "Paperback", "cover": "https://new", "editions": 30, "source": "Open Library",
            "asin": "B0DUNE0001", "minutes": 1265, "audible_cover": "https://aud",
        },
    ]  # fmt: skip
    current = book(
        year=2000, page_count=300, physical_format="Hardcover", cover="https://mine",
        store_url="https://www.audible.com/pd/OLD", audiobook_length="1h 0m",
    )  # fmt: skip
    changes = lookup_changes(current, candidates, OK, refresh=True)
    assert (changes["year"], changes["page_count"], changes["physical_format"]) == (1965, 412, "Paperback")
    assert changes["store_url"].endswith("B0DUNE0001") and changes["audiobook_length"] == "21h 5m"
    assert "cover" not in changes and "author" not in changes


def test_refresh_never_blanks_a_field_the_catalogs_lack() -> None:
    candidates = [{"title": "Dune", "author": "Frank Herbert", "editions": 1, "source": "s", "cover": ""}]
    changes = lookup_changes(book(year=2000, page_count=300), candidates, OK, refresh=True)
    assert changes["lookup_state"] == "ready"
    assert "year" not in changes and "page_count" not in changes


def test_author_conflict_parks_the_book_for_review() -> None:
    candidates = [{"title": "Dune", "author": "Someone Else", "editions": 1, "source": "s", "cover": ""}]
    changes = lookup_changes(book(), candidates, OK)
    assert changes["lookup_state"] == "verify"
    assert '"conflicts": [{"field": "author"' in changes["lookup_candidates"]


def test_no_exact_title_needs_verification() -> None:
    candidates = [
        {"title": "Dune Messiah", "author": "Frank Herbert", "editions": 1, "source": "s", "cover": ""}
    ]
    assert lookup_changes(book(), candidates, OK)["lookup_state"] == "verify"


def test_empty_results_distinguish_not_found_from_source_failure() -> None:
    assert lookup_changes(book(), [], OK)["lookup_state"] == "not_found"
    assert lookup_changes(book(), [], {**OK, "Open Library": "timeout"})["lookup_state"] == "failed"
    # Google Books failing alone never turns an honest empty result into a failure.
    assert lookup_changes(book(), [], {**OK, "Google Books": "timeout"})["lookup_state"] == "not_found"


def test_candidate_changes_flags_identity_change_and_applies_asin() -> None:
    changes, identity_changed = candidate_changes(
        book(),
        {
            "title": "Dune (Unabridged)",
            "author": "Frank Herbert",
            "asin": "B0DUNE0001",
            "minutes": 61,
            "junk": 1,
        },
    )
    assert identity_changed
    assert changes["title"] == "Dune (Unabridged)"
    assert changes["store_url"].endswith("B0DUNE0001")
    assert changes["audiobook_length"] == "1h 1m"
    assert "junk" not in changes


def test_candidate_changes_ignores_untrusted_shapes() -> None:
    changes, identity_changed = candidate_changes(
        book(), {"title": 5, "year": "1965", "asin": "nope", "physical_format": "Scroll"}
    )
    assert not identity_changed
    assert changes == {"lookup_state": "accepted"}


def test_envelope_parser_accepts_legacy_list_and_garbage() -> None:
    assert parse_lookup_envelope('[{"title": "x"}]') == {"candidates": [{"title": "x"}]}
    assert parse_lookup_envelope("not json") == {"candidates": []}
    assert parse_lookup_envelope(None) == {}
