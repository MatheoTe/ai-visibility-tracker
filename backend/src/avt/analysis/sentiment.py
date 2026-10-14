"""Sentiment and structured extraction.

``SentimentAnalyzer`` implementations receive the answer plus the brands the
deterministic pass already found, and return per-brand sentiment. Detection
stays deterministic and cheap; only tone - which genuinely needs language
understanding - goes to an LLM.

* :class:`LLMSentimentAnalyzer` - Claude Haiku with a JSON-schema constrained
  output (``output_config.format``), validated again with Pydantic.
* :class:`LexiconSentimentAnalyzer` - offline, deterministic; used with the fake
  provider, in tests, and as the fallback when the LLM call fails.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel, ValidationError

from avt.analysis.detection import BrandHit
from avt.domain import Sentiment
from avt.providers.pricing import estimate_cost

SentimentLabel = Literal["positive", "neutral", "negative", "mixed"]


@dataclass(frozen=True, slots=True)
class BrandSentiment:
    label: Sentiment
    score: float  # -1 .. 1
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class SentimentReport:
    analyzer: str
    overall: Sentiment
    brands: dict[str, BrandSentiment]  # keyed by brand name
    recommended_brand: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal(0)


class SentimentAnalyzer(Protocol):
    name: str

    async def analyze(self, text: str, hits: list[BrandHit]) -> SentimentReport: ...


def label_for(score: float) -> Sentiment:
    if score >= 0.2:
        return Sentiment.POSITIVE
    if score <= -0.2:
        return Sentiment.NEGATIVE
    return Sentiment.NEUTRAL


# ----------------------------------------------------------------------------- lexicon

POSITIVE_WORDS = frozenset(
    [
        "best",
        "excellent",
        "intuitive",
        "powerful",
        "affordable",
        "reliable",
        "popular",
        "recommended",
        "recommend",
        "top",
        "leading",
        "great",
        "easy",
        "generous",
        "strong",
        "robust",
        "favorite",
        "favourite",
        "loved",
        "value",
        "scales",
        "safer",
        "standout",
        "solid",
        "clean",
        "simple",
        "flexible",
    ]
)
NEGATIVE_WORDS = frozenset(
    [
        "expensive",
        "clunky",
        "limited",
        "outdated",
        "steep",
        "complex",
        "lacking",
        "poor",
        "slow",
        "buggy",
        "frustrating",
        "confusing",
        "pricey",
        "overpriced",
        "weak",
        "dated",
        "difficult",
        "hard",
        "cumbersome",
        "unreliable",
        "however",
    ]
)
NEGATORS = frozenset({"not", "no", "never", "isn't", "isnt", "doesn't", "doesnt", "hardly"})
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_WORD = re.compile(r"[a-z']+")


def lexicon_score(sentence: str) -> float:
    words = _WORD.findall(sentence.lower())
    pos = neg = 0
    for i, w in enumerate(words):
        polarity = 1 if w in POSITIVE_WORDS else -1 if w in NEGATIVE_WORDS else 0
        if polarity and any(p in NEGATORS for p in words[max(0, i - 3) : i]):
            polarity = -polarity
        pos += polarity > 0
        neg += polarity < 0
    if pos == neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg + 1)


class LexiconSentimentAnalyzer:
    name = "lexicon-v1"

    async def analyze(self, text: str, hits: list[BrandHit]) -> SentimentReport:
        sentences = [(m.start(), m.end(), m.group(0)) for m in _SENTENCE.finditer(text)]
        brands: dict[str, BrandSentiment] = {}
        for hit in hits:
            # Score the sentence containing each mention plus the one right after it
            # (list items usually put the verdict in the next sentence).
            scores: list[float] = []
            evidence = ""
            for span in hit.spans:
                for idx, (s, e, sentence) in enumerate(sentences):
                    if s <= span.start < e:
                        window = sentence
                        if idx + 1 < len(sentences) and "\n" not in text[e : sentences[idx + 1][0] + 1]:
                            window += " " + sentences[idx + 1][2]
                        scores.append(lexicon_score(window))
                        evidence = evidence or window.strip()[:200]
                        break
            score = round(sum(scores) / len(scores), 3) if scores else 0.0
            brands[hit.brand.name] = BrandSentiment(label_for(score), score, evidence)
        overall_score = sum(b.score for b in brands.values()) / len(brands) if brands else 0.0
        top = hits[0].brand.name if hits else None
        return SentimentReport(self.name, label_for(overall_score), brands, top)


# ----------------------------------------------------------------------------- LLM


class _LLMBrand(BaseModel):
    brand: str
    sentiment: SentimentLabel
    score: float
    evidence: str


class _LLMOutput(BaseModel):
    brands: list[_LLMBrand]
    overall_tone: SentimentLabel
    recommended_brand: str | None


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "brands": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "brand": {"type": "string"},
                    "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative", "mixed"]},
                    "score": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["brand", "sentiment", "score", "evidence"],
                "additionalProperties": False,
            },
        },
        "overall_tone": {"type": "string", "enum": ["positive", "neutral", "negative", "mixed"]},
        "recommended_brand": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["brands", "overall_tone", "recommended_brand"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You analyse answers produced by AI assistants for brand-visibility reporting. "
    "For each listed brand, judge how the answer portrays it: positive, neutral, negative, "
    "or mixed, with a score from -1 (very negative) to 1 (very positive) and a short quote "
    "as evidence. Judge only what the answer says, not your own opinion of the brand. "
    "recommended_brand is the brand the answer most clearly recommends, or null."
)


class LLMSentimentAnalyzer:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model
        self.name = f"llm:{model}"

    async def analyze(self, text: str, hits: list[BrandHit]) -> SentimentReport:
        if not hits:
            return SentimentReport(self.name, Sentiment.NEUTRAL, {})
        brand_list = "\n".join(f"- {h.brand.name}" for h in hits)
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Brands:\n{brand_list}\n\n<answer>\n{text}\n</answer>",
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        raw = next((b.text for b in message.content if b.type == "text"), "")
        try:
            parsed = _LLMOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"analyzer returned invalid JSON: {exc}") from exc

        known = {h.brand.name.casefold(): h.brand.name for h in hits}
        brands = {
            known[b.brand.casefold()]: BrandSentiment(
                Sentiment(b.sentiment), max(-1.0, min(1.0, b.score)), b.evidence[:300]
            )
            for b in parsed.brands
            if b.brand.casefold() in known
        }
        usage = message.usage
        return SentimentReport(
            analyzer=self.name,
            overall=Sentiment(parsed.overall_tone),
            brands=brands,
            recommended_brand=parsed.recommended_brand,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=estimate_cost(self._model, usage.input_tokens, usage.output_tokens),
        )


class FallbackSentimentAnalyzer:
    """Try the primary analyzer; on any failure degrade to the lexicon one.

    Sentiment is the least critical field of an analysis - it must never block
    mention / position / source data from landing.
    """

    def __init__(self, primary: SentimentAnalyzer, fallback: SentimentAnalyzer) -> None:
        self._primary = primary
        self._fallback = fallback
        self.name = primary.name

    async def analyze(self, text: str, hits: list[BrandHit]) -> SentimentReport:
        try:
            return await self._primary.analyze(text, hits)
        except Exception:  # noqa: BLE001 - deliberately broad: any failure degrades
            report = await self._fallback.analyze(text, hits)
            return SentimentReport(
                analyzer=f"{report.analyzer}(fallback)",
                overall=report.overall,
                brands=report.brands,
                recommended_brand=report.recommended_brand,
            )
