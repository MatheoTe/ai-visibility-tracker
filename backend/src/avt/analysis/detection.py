"""Deterministic brand / competitor detection.

Two passes over the normalised answer:

1. **Exact** - every alias variant compiled into one word-bounded regex per brand.
   Separators inside multi-word names are flexible (``hub nest`` matches
   "Hub Nest", "Hub-Nest" and "HubNest").
2. **Fuzzy** - sliding windows of 1-3 tokens compared with RapidFuzz against the
   compacted alias, to catch typos ("Pipewse"). Only for aliases of 5+ characters,
   with a high threshold, and never over text an exact match already claimed -
   short names fuzz-match far too much ordinary English.

Overlapping hits are resolved longest-span-first, so "Nimbus CRM Pro" beats
"Nimbus CRM". Brands are then ranked by first appearance: that rank is the
``position`` the dashboard reports (1 = mentioned first).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from avt.analysis.normalize import alias_variants, compact, normalize_with_offsets

FUZZY_MIN_LEN = 5
FUZZY_THRESHOLD = 90.0
_DERIVED_SUFFIXES = frozenset({"s", "es", "er", "ers", "ing", "ed"})
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'\-.]*[a-z0-9]|[a-z0-9]")


@dataclass(frozen=True, slots=True)
class BrandSpec:
    id: uuid.UUID
    name: str
    aliases: tuple[str, ...] = ()
    is_primary: bool = False

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]


@dataclass(frozen=True, slots=True)
class Span:
    start: int  # offsets into the ORIGINAL text, end exclusive
    end: int
    matched: str
    kind: str  # "exact" | "fuzzy"


@dataclass(slots=True)
class BrandHit:
    brand: BrandSpec
    spans: list[Span] = field(default_factory=list)
    position: int = 0

    @property
    def first_offset(self) -> int:
        return self.spans[0].start

    @property
    def mention_count(self) -> int:
        return len(self.spans)


@dataclass(frozen=True, slots=True)
class _Candidate:
    brand_idx: int
    nstart: int  # offsets into the normalised text
    nend: int
    kind: str


def _pattern(variants: Iterable[str]) -> re.Pattern[str]:
    parts = []
    for v in sorted(variants, key=len, reverse=True):
        words = [re.escape(w) for w in v.split(" ")]
        parts.append(r"[\s\-_.]?".join(words))
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")


def _is_derived(word: str, stem: str) -> bool:
    """True for inflections of the brand name ("pipewiser", "hubnesting")."""
    return any(
        word.endswith(suffix) and word[: -len(suffix)] in (stem, stem[:-1]) for suffix in _DERIVED_SUFFIXES
    )


class BrandDetector:
    """Compiled once per workspace brand set, reused for every response."""

    def __init__(self, brands: Sequence[BrandSpec], *, fuzzy: bool = True) -> None:
        self.brands = list(brands)
        self.fuzzy = fuzzy
        self._patterns: list[re.Pattern[str]] = []
        self._fuzzy_targets: list[list[tuple[str, int]]] = []
        for brand in self.brands:
            variants: set[str] = set()
            for name in brand.all_names():
                variants |= alias_variants(name)
            self._patterns.append(_pattern(variants))
            targets = {(compact(v), len(v.split(" "))) for v in variants}
            self._fuzzy_targets.append([(c, n) for c, n in targets if len(c) >= FUZZY_MIN_LEN and n <= 3])

    def detect(self, text: str) -> list[BrandHit]:
        norm, index = normalize_with_offsets(text)
        candidates: list[_Candidate] = []
        for i, pattern in enumerate(self._patterns):
            candidates.extend(_Candidate(i, m.start(), m.end(), "exact") for m in pattern.finditer(norm))
        if self.fuzzy:
            candidates.extend(self._fuzzy_candidates(norm, candidates))

        # Longest span wins; on ties, exact beats fuzzy, then earlier wins.
        candidates.sort(key=lambda c: (-(c.nend - c.nstart), c.kind != "exact", c.nstart))
        taken: list[tuple[int, int]] = []
        kept: list[_Candidate] = []
        for c in candidates:
            if any(c.nstart < e and s < c.nend for s, e in taken):
                continue
            taken.append((c.nstart, c.nend))
            kept.append(c)

        hits: dict[int, BrandHit] = {}
        for c in sorted(kept, key=lambda c: c.nstart):
            start, end = index[c.nstart], index[c.nend - 1] + 1
            hit = hits.setdefault(c.brand_idx, BrandHit(brand=self.brands[c.brand_idx]))
            hit.spans.append(Span(start=start, end=end, matched=text[start:end], kind=c.kind))

        ordered = sorted(hits.values(), key=lambda h: h.first_offset)
        for pos, hit in enumerate(ordered, start=1):
            hit.position = pos
        return ordered

    def _fuzzy_candidates(self, norm: str, exact: list[_Candidate]) -> list[_Candidate]:
        # Token windows are built once per text and bucketed by compacted length, so
        # each alias only compares against windows within +-2 characters of it.
        tokens = [(m.start(), m.end(), compact(m.group())) for m in _TOKEN.finditer(norm)]
        claimed = [any(s < c.nend and c.nstart < e for c in exact) for s, e, _ in tokens]
        by_len: dict[int, list[tuple[int, int, str]]] = {}
        max_width = 1 + max((n for targets in self._fuzzy_targets for _, n in targets), default=0)
        for width in range(1, max_width + 1):
            for t in range(len(tokens) - width + 1):
                if any(claimed[t : t + width]):
                    continue
                window = "".join(tok[2] for tok in tokens[t : t + width])
                by_len.setdefault(len(window), []).append((tokens[t][0], tokens[t + width - 1][1], window))

        found: list[_Candidate] = []
        for b_idx, targets in enumerate(self._fuzzy_targets):
            for target, _ in targets:
                for length in range(len(target) - 2, len(target) + 3):
                    for s, e, window in by_len.get(length, ()):
                        if window == target or _is_derived(window, target):
                            continue
                        if fuzz.ratio(window, target) >= FUZZY_THRESHOLD:
                            found.append(_Candidate(b_idx, s, e, "fuzzy"))
        return found
