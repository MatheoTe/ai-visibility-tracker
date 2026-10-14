from __future__ import annotations

import pytest

from avt.analysis.urls import extract_sources, normalize_url, registrable_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.G2.com/categories/crm/", "https://g2.com/categories/crm"),
        ("http://example.com", "https://example.com/"),
        ("https://example.com/a?utm_source=x&id=7&gclid=y#frag", "https://example.com/a?id=7"),
        ("https://example.com:443/path", "https://example.com/path"),
        ("www.capterra.com/crm).", "https://capterra.com/crm"),
        ("https://localhost/x", None),
    ],
)
def test_normalize_url(raw: str, expected: str | None) -> None:
    assert normalize_url(raw) == expected


def test_registrable_domain_uses_public_suffix_list() -> None:
    assert registrable_domain("blog.example.co.uk") == "example.co.uk"
    assert registrable_domain("news.ycombinator.com") == "ycombinator.com"


def test_extracts_markdown_bare_and_www_in_reading_order() -> None:
    text = (
        "See [G2](https://www.g2.com/crm?utm_medium=ai) and https://reddit.com/r/sales.\n"
        "Also www.techradar.com/best-crm, plus (https://zapier.com/blog/best-crm-app/)."
    )
    sources = extract_sources(text)
    assert [s.url for s in sources] == [
        "https://g2.com/crm",
        "https://reddit.com/r/sales",
        "https://techradar.com/best-crm",
        "https://zapier.com/blog/best-crm-app",
    ]
    assert [s.rank for s in sources] == [1, 2, 3, 4]
    assert {s.origin for s in sources} == {"inline"}


def test_dedupes_inline_and_provider_citations() -> None:
    text = "Per https://www.g2.com/crm the leader is X."
    sources = extract_sources(text, ["https://g2.com/crm/", "https://www.forbes.com/advisor/crm"])
    assert [(s.url, s.origin) for s in sources] == [
        ("https://g2.com/crm", "inline"),
        ("https://forbes.com/advisor/crm", "provider"),
    ]
    assert sources[1].domain == "forbes.com"


def test_ignores_text_without_urls() -> None:
    assert extract_sources("No links, just Salesforce and e.g. something.") == []
