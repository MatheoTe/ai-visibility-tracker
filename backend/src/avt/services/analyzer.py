"""Runs the analysis stage for stored responses and persists the results."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import anthropic
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from avt.analysis.pipeline import ANALYSIS_VERSION, AnalysisResult, analyze_response
from avt.analysis.sentiment import (
    FallbackSentimentAnalyzer,
    LexiconSentimentAnalyzer,
    LLMSentimentAnalyzer,
    SentimentAnalyzer,
)
from avt.config import Settings
from avt.db.base import Database
from avt.db.models import Analysis, BrandMention, CitedSource, Response
from avt.logging import get_logger
from avt.metrics import ANALYSES
from avt.services.aggregates import rebuild_daily_stats
from avt.services.brand_cache import BrandCache
from avt.services.executor import finalize_if_done

log = get_logger(__name__)


def build_sentiment_analyzer(settings: Settings) -> SentimentAnalyzer:
    """Haiku with structured output when a key is configured, lexicon otherwise."""
    lexicon = LexiconSentimentAnalyzer()
    if settings.use_llm_analyzer and settings.anthropic_api_key and not settings.force_fake_providers:
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(), max_retries=2, timeout=60
        )
        return FallbackSentimentAnalyzer(
            LLMSentimentAnalyzer(client, settings.anthropic_analysis_model), lexicon
        )
    return lexicon


class AnalysisService:
    def __init__(self, db: Database, brands: BrandCache, sentiment: SentimentAnalyzer) -> None:
        self.db = db
        self.brands = brands
        self.sentiment = sentiment

    async def analyze(
        self, response_id: int, *, version: int = ANALYSIS_VERSION, finalize: bool = True
    ) -> int | None:
        """Analyse one response. Idempotent on (response_id, version)."""
        async with self.db.session() as session:
            response = await session.get(Response, response_id)
            if response is None:
                return None
            existing = (
                await session.execute(
                    select(Analysis.id).where(
                        Analysis.response_id == response_id, Analysis.version == version
                    )
                )
            ).scalar_one_or_none()
            brands = None if existing else await self.brands.get(session, response.workspace_id)

        analysis_id = existing
        if brands is not None:
            result = await analyze_response(
                response.text, list(response.provider_citations), brands.detector, self.sentiment
            )
            analysis_id = await self._persist(response, result)
        if finalize:
            await finalize_if_done(self.db, response.run_id, response.workspace_id)
        return analysis_id

    async def _persist(self, response: Response, result: AnalysisResult) -> int | None:
        primary = result.primary
        async with self.db.session() as session:
            analysis_id = (
                await session.execute(
                    insert(Analysis)
                    .values(
                        response_id=response.id,
                        version=result.version,
                        analyzer=result.analyzer,
                        overall_sentiment=result.overall_sentiment.value,
                        primary_mentioned=primary is not None,
                        primary_position=primary.hit.position if primary else None,
                        detail=result.detail(),
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        cost_usd=result.cost_usd,
                    )
                    .on_conflict_do_nothing(index_elements=["response_id", "version"])
                    .returning(Analysis.id)
                )
            ).scalar_one_or_none()
            if analysis_id is None:  # a concurrent worker won the race
                await session.rollback()
                return None
            common = {
                "analysis_id": analysis_id,
                "analysis_version": result.version,
                "workspace_id": response.workspace_id,
                "response_id": response.id,
                "model_key": response.model_key,
                "day": response.day,
            }
            if result.mentions:
                await session.execute(
                    insert(BrandMention),
                    [
                        {
                            **common,
                            "brand_id": m.hit.brand.id,
                            "prompt_id": response.prompt_id,
                            "position": m.hit.position,
                            "mention_count": min(m.hit.mention_count, 32767),
                            "first_offset": m.hit.first_offset,
                            "sentiment": m.sentiment.label.value,
                            "sentiment_score": m.sentiment.score,
                        }
                        for m in result.mentions
                    ],
                )
            if result.sources:
                await session.execute(
                    insert(CitedSource),
                    [
                        {**common, "url": s.url, "domain": s.domain, "rank": s.rank, "origin": s.origin}
                        for s in result.sources
                    ],
                )
            await session.commit()
        ANALYSES.labels(result.analyzer).inc()
        return analysis_id

    async def reanalyze_range(
        self,
        workspace_id: uuid.UUID,
        date_from: dt.date,
        date_to: dt.date,
        *,
        version: int = ANALYSIS_VERSION,
        concurrency: int = 16,
        page_size: int = 1000,
    ) -> int:
        """Backfill analyses for a date range with the current analysis version,
        then rebuild the rollups for those days. Raw responses are untouched."""
        self.brands.invalidate(workspace_id)
        gate = asyncio.Semaphore(concurrency)
        done, last_id = 0, 0

        async def one(rid: int) -> None:
            async with gate:
                await self.analyze(rid, version=version, finalize=False)

        while True:
            async with self.db.session() as session:
                ids = list(
                    (
                        await session.execute(
                            select(Response.id)
                            .where(
                                Response.workspace_id == workspace_id,
                                Response.day.between(date_from, date_to),
                                Response.id > last_id,
                            )
                            .order_by(Response.id)
                            .limit(page_size)
                        )
                    ).scalars()
                )
            if not ids:
                break
            await asyncio.gather(*(one(rid) for rid in ids))
            done += len(ids)
            last_id = ids[-1]

        days = [date_from + dt.timedelta(days=i) for i in range((date_to - date_from).days + 1)]
        async with self.db.session() as session:
            await rebuild_daily_stats(session, workspace_id, days, version)
        log.info("reanalysis.done", workspace_id=str(workspace_id), responses=done, version=version)
        return done
