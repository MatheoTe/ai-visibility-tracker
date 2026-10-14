"""The analysis stage: raw response -> structured, versioned analysis.

Bump :data:`ANALYSIS_VERSION` whenever detection, extraction or sentiment logic
changes in a way that changes results. New analyses are written next to the old
ones (``UNIQUE(response_id, version)``), aggregates are rebuilt from the current
version, and raw responses are never touched - so a re-analysis is a backfill
job, not a migration, and can be compared against the previous version.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from avt.analysis.detection import BrandDetector, BrandHit
from avt.analysis.sentiment import BrandSentiment, SentimentAnalyzer
from avt.analysis.urls import Source, extract_sources
from avt.domain import Sentiment

ANALYSIS_VERSION = 2
# v1: initial release.
# v2: brand names inside URLs / bare domains are no longer counted as mentions.


@dataclass(frozen=True, slots=True)
class MentionResult:
    hit: BrandHit
    sentiment: BrandSentiment


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    version: int
    analyzer: str
    mentions: list[MentionResult]
    sources: list[Source]
    overall_sentiment: Sentiment
    recommended_brand: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal

    @property
    def primary(self) -> MentionResult | None:
        return next((m for m in self.mentions if m.hit.brand.is_primary), None)

    def detail(self) -> dict[str, Any]:
        """JSON stored on the analysis row - everything the drill-down UI needs."""
        return {
            "recommended_brand": self.recommended_brand,
            "mentions": [
                {
                    "brand_id": str(m.hit.brand.id),
                    "brand": m.hit.brand.name,
                    "position": m.hit.position,
                    "sentiment": m.sentiment.label.value,
                    "score": m.sentiment.score,
                    "evidence": m.sentiment.evidence,
                    "spans": [
                        {"start": s.start, "end": s.end, "kind": s.kind, "matched": s.matched}
                        for s in m.hit.spans
                    ],
                }
                for m in self.mentions
            ],
            "sources": [
                {"url": s.url, "domain": s.domain, "rank": s.rank, "origin": s.origin} for s in self.sources
            ],
        }


async def analyze_response(
    text: str,
    provider_citations: list[str],
    detector: BrandDetector,
    sentiment: SentimentAnalyzer,
) -> AnalysisResult:
    hits = detector.detect(text)
    sources = extract_sources(text, provider_citations)
    report = await sentiment.analyze(text, hits)
    neutral = BrandSentiment(Sentiment.NEUTRAL, 0.0)
    mentions = [MentionResult(h, report.brands.get(h.brand.name, neutral)) for h in hits]
    return AnalysisResult(
        version=ANALYSIS_VERSION,
        analyzer=report.analyzer,
        mentions=mentions,
        sources=sources,
        overall_sentiment=report.overall,
        recommended_brand=report.recommended_brand,
        input_tokens=report.input_tokens,
        output_tokens=report.output_tokens,
        cost_usd=report.cost_usd,
    )
