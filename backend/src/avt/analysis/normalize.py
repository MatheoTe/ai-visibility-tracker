"""Text normalisation that remembers where every character came from.

Matching happens on a folded copy of the answer (case-folded, accents stripped,
typographic punctuation unified, whitespace collapsed) but highlights must point
at the *original* text, so normalisation also returns an index map from each
normalised character back to its source offset.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT_MAP = {
    chr(0x2018): "'",  # left single quote
    chr(0x2019): "'",  # right single quote
    chr(0x201C): '"',  # left double quote
    chr(0x201D): '"',  # right double quote
    chr(0x2013): "-",  # en dash
    chr(0x2014): "-",  # em dash
    chr(0x00A0): " ",  # no-break space
    "&": " and ",
}


def _fold(ch: str) -> str:
    mapped = _PUNCT_MAP.get(ch)
    if mapped is not None:
        return mapped
    decomposed = unicodedata.normalize("NFKD", ch)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Return ``(normalized, index_map)`` where ``index_map[i]`` is the offset in
    ``text`` of normalised character ``i``."""
    out: list[str] = []
    index: list[int] = []
    prev_space = True  # also trims leading whitespace
    for i, ch in enumerate(text):
        for c in _fold(ch):
            if c.isspace():
                if prev_space:
                    continue
                c = " "
                prev_space = True
            else:
                prev_space = False
            out.append(c)
            index.append(i)
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def normalize(text: str) -> str:
    return normalize_with_offsets(text)[0]


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def alias_variants(alias: str) -> set[str]:
    """Spellings an alias is commonly written as: ``HubNest`` -> {"hubnest", "hub nest"}."""
    variants = {normalize(alias)}
    split = _CAMEL.sub(" ", alias)
    if split != alias:
        variants.add(normalize(split))
    return {v for v in variants if v}


def compact(text: str) -> str:
    """Letters and digits only - the representation fuzzy matching compares."""
    return _NON_ALNUM.sub("", text)
