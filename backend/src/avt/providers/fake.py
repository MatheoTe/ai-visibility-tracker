"""Deterministic fake provider.

Produces realistic, *reproducible* answers: the same (provider, model, prompt, day,
attempt) always yields the same text. Brand "strength" drifts slowly over time so a
backfilled history shows real trends, each provider has its own bias so per-model
comparisons are meaningful, and a configurable fraction of calls fail with 429/5xx
so the retry, backoff and circuit-breaker paths are exercised in tests and demos.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence

from avt.providers.base import (
    GenerationRequest,
    GenerationResult,
    RateLimitedError,
    TransientProviderError,
)

# Fictional filler brands so answers look like a real market even when the
# workspace only tracks a couple of names.
FILLER_BRANDS: tuple[str, ...] = (
    "Orbitly",
    "LeadLoom",
    "Salesgrid",
    "Nimbus CRM",
    "Dealflow",
    "Contactly",
    "Pipedream One",
    "Relata",
)

SOURCE_DOMAINS: tuple[str, ...] = (
    "https://www.g2.com/categories/crm",
    "https://www.capterra.com/customer-relationship-management-software/",
    "https://www.techradar.com/best/best-crm-software",
    "https://www.reddit.com/r/smallbusiness/comments/crm_recommendations",
    "https://www.forbes.com/advisor/business/software/best-crm-small-business/",
    "https://www.pcmag.com/picks/the-best-crm-software",
    "https://www.trustradius.com/crm",
    "https://zapier.com/blog/best-crm-app/",
    "https://www.gartner.com/reviews/market/crm",
    "https://news.ycombinator.com/item?id=4012345",
)

DESCRIPTORS: tuple[str, ...] = (
    "pipeline management with drag-and-drop deal stages",
    "built-in email sequences and lead scoring",
    "a generous free tier for small teams",
    "deep integrations with accounting and support tools",
    "AI-assisted forecasting and activity capture",
    "a mobile app that field sales teams actually use",
    "simple setup and a clean interface",
    "advanced reporting and custom dashboards",
)

POSITIVE = (
    "It is widely recommended and very easy to use.",
    "Reviewers consistently describe it as reliable and intuitive.",
    "It offers excellent value and a powerful automation engine.",
    "A popular, well-supported choice that scales nicely.",
)
NEUTRAL = (
    "It covers the essentials without many surprises.",
    "A reasonable option depending on your existing stack.",
    "Worth evaluating alongside the others in a trial.",
)
NEGATIVE = (
    "However, it can get expensive as you add seats.",
    "Some users find the interface clunky and the reporting limited.",
    "Support is slow and the setup is complex for small teams.",
)

INTROS = (
    'Here are some of the strongest options for "{prompt}":',
    'Based on recent reviews and comparisons, these stand out for "{prompt}":',
    'There is no single answer, but for "{prompt}" the usual shortlist is:',
)

PROVIDER_BIAS = {"anthropic": 0.11, "openai": 0.29, "gemini": 0.53, "perplexity": 0.71}

SleepFn = Callable[[float], Awaitable[None]]


def stable_int(*parts: object) -> int:
    digest = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _unit(*parts: object) -> float:
    return stable_int(*parts) / 2**64


def brand_strength(brand: str, provider: str, day_ordinal: int) -> float:
    """Deterministic strength in ~[0.05, 1.0] with a slow per-brand trend."""
    base = 0.35 + 0.55 * _unit("base", brand)
    bias = 0.25 * (_unit("bias", brand, provider) - 0.5) + 0.1 * PROVIDER_BIAS.get(provider, 0)
    # Each brand drifts up or down with its own slope, plus a slow seasonal wave.
    slope = (_unit("slope", brand) - 0.45) * 0.012
    wave = 0.08 * math.sin(day_ordinal / 9.0 + 6 * _unit("phase", brand))
    trend = slope * (day_ordinal % 365)
    return max(0.05, min(1.0, base + bias + wave + trend))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _variant(name: str, rng: random.Random) -> str:
    """Occasionally mention a brand the way people actually write it."""
    roll = rng.random()
    if roll < 0.07 and " " not in name:
        return name.lower()
    if roll < 0.10 and len(name) > 6:
        i = rng.randrange(2, len(name) - 2)
        return name[:i] + name[i + 1 :]  # a dropped letter: fuzzy matching should catch it
    return name


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        latency_ms: tuple[int, int] = (40, 250),
        error_rate: float = 0.03,
        sleep: SleepFn = asyncio.sleep,
        filler_brands: Sequence[str] = FILLER_BRANDS,
    ) -> None:
        self.name = name
        self._latency = latency_ms
        self._error_rate = error_rate
        self._sleep = sleep
        self._filler = tuple(filler_brands)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        seed = stable_int(self.name, request.model, request.prompt, request.day, request.attempt)
        rng = random.Random(seed)
        lo, hi = self._latency
        if hi > 0:
            await self._sleep(rng.uniform(lo, hi) / 1000)
        fault = rng.random()
        if fault < self._error_rate * 0.6:
            raise RateLimitedError("fake 429: rate limited", status_code=429, retry_after=0.5)
        if fault < self._error_rate:
            raise TransientProviderError("fake 503: upstream overloaded", status_code=503)

        text, citations = self._compose(request, rng)
        input_tokens = max(8, len(request.prompt) // 4 + 24)
        output_tokens = max(16, len(text) // 4)
        return GenerationResult(
            text=text,
            model_name=request.model,
            input_tokens=input_tokens,
            output_tokens=min(output_tokens, request.max_output_tokens),
            citations=citations,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------------ composition

    def _compose(self, request: GenerationRequest, rng: random.Random) -> tuple[str, list[str]]:
        day = request.day.toordinal()
        universe = list(dict.fromkeys([*request.simulation_brands, *self._filler]))
        # Prompt affinity: some prompts favour some brands (stable per prompt).
        scored = [
            (
                brand_strength(b, self.name, day)
                * (0.6 + 0.8 * _unit("affinity", b, request.prompt))
                * (0.75 + 0.5 * rng.random()),
                b,
            )
            for b in universe
        ]
        scored.sort(reverse=True)
        count = rng.randint(3, min(6, len(scored)))
        chosen = [b for score, b in scored[:count] if score > 0.22] or [scored[0][1]]

        lines = [rng.choice(INTROS).format(prompt=request.prompt.strip().rstrip("?")), ""]
        for i, brand in enumerate(chosen, start=1):
            strength = brand_strength(brand, self.name, day)
            mood = rng.random() + (strength - 0.5) * 0.8
            remark = (
                rng.choice(POSITIVE)
                if mood > 0.75
                else rng.choice(NEGATIVE)
                if mood < 0.25
                else rng.choice(NEUTRAL)
            )
            lines.append(f"{i}. **{_variant(brand, rng)}** - known for {rng.choice(DESCRIPTORS)}. {remark}")
        lines.append("")
        lines.append(
            f"If budget is the main constraint, start with {chosen[-1]}; if you need room to grow, "
            f"{chosen[0]} is the safer long-term bet."
        )

        n_sources = rng.randint(1, 4)
        sources = rng.sample(SOURCE_DOMAINS, n_sources)
        if rng.random() < 0.5:
            sources.append(f"https://www.{_slug(chosen[0])}.com/pricing?utm_source=ai")
        citations: list[str] = []
        if self.name == "perplexity":
            # Perplexity-style: citations come back out of band, referenced inline as [n].
            citations = sources
            lines.append("")
            lines.append(" ".join(f"[{i}]" for i in range(1, len(sources) + 1)))
        else:
            lines.append("")
            lines.append("Sources:")
            lines.extend(f"- {url}" for url in sources)
        return "\n".join(lines), citations
