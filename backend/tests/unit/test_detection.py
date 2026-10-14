from __future__ import annotations

import uuid

import pytest

from avt.analysis.detection import BrandDetector, BrandSpec
from avt.analysis.normalize import alias_variants, normalize, normalize_with_offsets


def brand(name: str, *aliases: str, primary: bool = False) -> BrandSpec:
    return BrandSpec(id=uuid.uuid4(), name=name, aliases=aliases, is_primary=primary)


LUMORA = brand("Lumora CRM", "Lumora", primary=True)
PIPEWISE = brand("Pipewise")
HUBNEST = brand("HubNest")
CLIENTRA = brand("Clientra")


@pytest.fixture
def detector() -> BrandDetector:
    return BrandDetector([LUMORA, PIPEWISE, HUBNEST, CLIENTRA])


def test_normalize_folds_case_accents_and_typography() -> None:
    assert normalize("  Café CRM’s  “best”  ") == 'cafe crm\'s "best"'


def test_offsets_map_back_to_original_text() -> None:
    text = "Ça  marche: ÉLAN"
    norm, index = normalize_with_offsets(text)
    start = norm.index("elan")
    assert text[index[start] : index[start + 3] + 1] == "ÉLAN"


def test_alias_variants_split_camel_case() -> None:
    assert alias_variants("HubNest") == {"hubnest", "hub nest"}


def test_exact_matches_are_ranked_by_first_appearance(detector: BrandDetector) -> None:
    text = "1. Pipewise is great.\n2. Lumora CRM is simple.\n3. Clientra.\nAgain, Pipewise."
    hits = detector.detect(text)
    assert [h.brand.name for h in hits] == ["Pipewise", "Lumora CRM", "Clientra"]
    assert [h.position for h in hits] == [1, 2, 3]
    assert hits[0].mention_count == 2


def test_spans_point_at_original_text(detector: BrandDetector) -> None:
    text = "We love **LUMORA crm** for small teams."
    (hit,) = detector.detect(text)
    span = hit.spans[0]
    assert text[span.start : span.end] == "LUMORA crm"
    assert span.kind == "exact"


def test_flexible_separators_match_spelling_variants(detector: BrandDetector) -> None:
    for written in ("HubNest", "Hub Nest", "hub-nest", "hubnest"):
        hits = detector.detect(f"Consider {written} as well.")
        assert [h.brand.name for h in hits] == ["HubNest"], written


def test_word_boundaries_prevent_substring_matches(detector: BrandDetector) -> None:
    assert detector.detect("The pipewiser tool and hubnesting are unrelated.") == []


def test_longest_alias_wins_overlap(detector: BrandDetector) -> None:
    (hit,) = detector.detect("Lumora CRM leads the list.")
    assert hit.mention_count == 1
    assert hit.spans[0].matched == "Lumora CRM"


def test_fuzzy_catches_typos(detector: BrandDetector) -> None:
    hits = detector.detect("Many teams pick Pipewse or Clientraa for this.")
    by_name = {h.brand.name: h for h in hits}
    assert set(by_name) == {"Pipewise", "Clientra"}
    assert all(s.kind == "fuzzy" for h in hits for s in h.spans)


def test_fuzzy_ignores_short_names() -> None:
    short = BrandDetector([brand("Zap")])
    assert short.detect("Use zip or zapp for that.") == []


def test_fuzzy_can_be_disabled() -> None:
    strict = BrandDetector([PIPEWISE], fuzzy=False)
    assert strict.detect("Pipewse is good") == []


def test_no_brands_no_hits(detector: BrandDetector) -> None:
    assert detector.detect("Nothing to see here.") == []


def test_brand_names_inside_urls_are_not_mentions(detector: BrandDetector) -> None:
    text = (
        "Try Clientra.\nSources:\n- https://www.clientra.com/pricing?utm_source=ai\n"
        "- www.pipewise.com/blog\n- see hubnest.com for details"
    )
    hits = detector.detect(text)
    assert [h.brand.name for h in hits] == ["Clientra"]
    assert hits[0].mention_count == 1
