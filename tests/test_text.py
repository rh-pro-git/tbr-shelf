from tbr_shelf.text import (
    asin_from_url,
    canonical_tag,
    format_runtime,
    is_asin,
    match_key,
    normalize_tags,
    pick_print_format,
    split_tags,
    strip_html,
    year_from,
)


def test_match_key_folds_accents_case_and_punctuation() -> None:
    assert match_key("Gabriel García Márquez") == "gabrielgarciamarquez"
    assert match_key("Don't Panic!") == match_key("dont panic")
    assert match_key("") == ""


def test_canonical_tag_title_cases_but_keeps_acronyms_and_hyphens() -> None:
    assert canonical_tag("space opera") == "Space Opera"
    assert canonical_tag("SF") == "SF"
    assert canonical_tag("sci-fi classics") == "Sci-Fi Classics"


def test_normalize_tags_splits_dedupes_and_rejoins() -> None:
    assert normalize_tags("fantasy; epic fantasy, FANTASY / dragons") == "Fantasy, Epic Fantasy, Dragons"
    assert normalize_tags(None) == ""
    assert split_tags("Fantasy, Dragons") == ["Fantasy", "Dragons"]


def test_pick_print_format_maps_catalog_wording_to_tiers() -> None:
    assert pick_print_format(["Mass Market Paperback"]) == "Mass Market"
    assert pick_print_format(["Hardback"]) == "Hardcover"
    assert pick_print_format(["trade pb"]) == "Paperback"
    assert pick_print_format(["ebook"]) is None
    assert pick_print_format(None) is None


def test_small_formatters() -> None:
    assert format_runtime(665) == "11h 5m"
    assert format_runtime(None) == ""
    assert year_from("2019-05-02") == 2019
    assert year_from("n/a") is None
    assert is_asin("B0ABCDEFGH")
    assert not is_asin("abc")
    assert asin_from_url("https://www.audible.com/pd/B0ABCDEFGH?x=1") == "B0ABCDEFGH"
    assert asin_from_url(None) is None


def test_strip_html_keeps_paragraph_breaks_and_unescapes() -> None:
    assert strip_html("<p>One &amp; two.</p><p>Three<br>four</p>") == "One & two.\nThree\nfour"
    assert strip_html(None) == ""
