"""Source / URL extraction and normalisation.

Answers cite sources as markdown links, bare URLs, ``www.`` hostnames, and (for
search-grounded providers) out-of-band citation lists. Everything is normalised so
the same page counts once: lower-cased host, ``www.`` dropped, fragments and
tracking parameters removed. Domains are reduced to the registrable domain using
the public suffix list bundled with ``tldextract`` (no network access), so
``blog.example.co.uk`` and ``example.co.uk`` aggregate together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

_MD_LINK = re.compile(r"\[[^\]]{0,300}\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_BARE_URL = re.compile(r"https?://[^\s<>\"'\]\[(){}|\\^`]+", re.IGNORECASE)
_WWW = re.compile(r"(?<![\w/.@])www\.[a-z0-9\-]+(?:\.[a-z0-9\-]+)+(?:/[^\s<>\"'\])]*)?", re.IGNORECASE)
_TRAILING = ".,;:!?*_'\""
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset({"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid"})


@dataclass(frozen=True, slots=True)
class Source:
    url: str
    domain: str
    rank: int
    origin: str  # "inline" (in the answer text) | "provider" (out-of-band citation)


def _strip_trailing(url: str) -> str:
    url = url.rstrip(_TRAILING)
    # Balance a trailing ")" that belongs to surrounding prose, not the URL.
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1].rstrip(_TRAILING)
    return url


def registrable_domain(host: str) -> str:
    ext = _EXTRACT(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return host.lower()


def normalize_url(raw: str) -> str | None:
    raw = _strip_trailing(raw.strip())
    if not raw:
        return None
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return None
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
        ]
    )
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit(("https", netloc, path, query, ""))


def extract_sources(text: str, provider_citations: list[str] | None = None) -> list[Source]:
    """Ordered, de-duplicated sources: inline ones in reading order, then provider ones."""
    found = [(m.start(1), m.group(1)) for m in _MD_LINK.finditer(text)]
    found += [(m.start(), m.group(0)) for m in _BARE_URL.finditer(text)]
    found += [(m.start(), m.group(0)) for m in _WWW.finditer(text)]
    found.sort(key=lambda item: item[0])

    seen: set[str] = set()
    out: list[Source] = []

    def add(raw: str, origin: str) -> None:
        url = normalize_url(raw)
        if url is None or url in seen:
            return
        seen.add(url)
        host = urlsplit(url).hostname or ""
        out.append(Source(url=url, domain=registrable_domain(host), rank=len(out) + 1, origin=origin))

    for _, raw in found:
        add(raw, "inline")
    for raw in provider_citations or []:
        add(raw, "provider")
    return out
