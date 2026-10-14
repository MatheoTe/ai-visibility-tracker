from __future__ import annotations

import uuid
from typing import Any

import pytest

from avt.analysis.detection import BrandDetector, BrandHit, BrandSpec
from avt.analysis.pipeline import ANALYSIS_VERSION, analyze_response
from avt.analysis.sentiment import (
    FallbackSentimentAnalyzer,
    LexiconSentimentAnalyzer,
    SentimentReport,
    lexicon_score,
)
from avt.domain import Sentiment

PRIMARY = BrandSpec(uuid.uuid4(), "Lumora", (), True)
RIVAL = BrandSpec(uuid.uuid4(), "Pipewise")


def test_lexicon_score_handles_negation() -> None:
    assert lexicon_score("It is reliable and intuitive.") > 0.3
    assert lexicon_score("It is expensive and clunky.") < -0.3
    assert lexicon_score("It is not reliable.") < 0
    assert lexicon_score("A tool.") == 0


async def test_lexicon_scores_each_brand_from_its_own_sentence() -> None:
    text = "1. **Lumora** - Excellent and reliable.\n2. **Pipewise** - known for X. However, it is expensive."
    hits = BrandDetector([PRIMARY, RIVAL]).detect(text)
    report = await LexiconSentimentAnalyzer().analyze(text, hits)
    assert report.brands["Lumora"].label is Sentiment.POSITIVE
    assert report.brands["Pipewise"].label is Sentiment.NEGATIVE


class _Boom:
    name = "llm:test"

    async def analyze(self, text: str, hits: list[BrandHit]) -> SentimentReport:
        raise RuntimeError("provider down")


async def test_fallback_analyzer_degrades_to_lexicon() -> None:
    analyzer = FallbackSentimentAnalyzer(_Boom(), LexiconSentimentAnalyzer())
    hits = BrandDetector([PRIMARY]).detect("Lumora is excellent.")
    report = await analyzer.analyze("Lumora is excellent.", hits)
    assert report.analyzer == "lexicon-v1(fallback)"
    assert report.brands["Lumora"].label is Sentiment.POSITIVE


async def test_pipeline_combines_detection_sources_and_sentiment() -> None:
    text = (
        "For small teams:\n1. **Pipewise** - popular and easy.\n2. **Lumora** - powerful.\n"
        "Sources:\n- https://www.g2.com/crm\n"
    )
    result = await analyze_response(
        text, ["https://forbes.com/a"], BrandDetector([PRIMARY, RIVAL]), LexiconSentimentAnalyzer()
    )
    assert result.version == ANALYSIS_VERSION
    assert [m.hit.brand.name for m in result.mentions] == ["Pipewise", "Lumora"]
    assert result.primary is not None
    assert result.primary.hit.position == 2
    assert [s.domain for s in result.sources] == ["g2.com", "forbes.com"]
    detail: dict[str, Any] = result.detail()
    assert detail["mentions"][1]["spans"][0]["matched"] == "Lumora"


@pytest.mark.parametrize("text", ["", "No brands here at all."])
async def test_pipeline_without_mentions(text: str) -> None:
    result = await analyze_response(text, [], BrandDetector([PRIMARY]), LexiconSentimentAnalyzer())
    assert result.mentions == []
    assert result.primary is None
    assert result.overall_sentiment is Sentiment.NEUTRAL
