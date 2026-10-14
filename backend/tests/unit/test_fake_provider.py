from __future__ import annotations

import datetime as dt

import pytest

from avt.providers.base import GenerationRequest, ProviderError
from avt.providers.fake import FakeProvider, brand_strength


async def _no_sleep(_: float) -> None:
    return None


def request(prompt: str = "best CRM for small businesses", **kw: object) -> GenerationRequest:
    base: dict[str, object] = {
        "prompt": prompt,
        "model": "claude-sonnet-5-5",
        "day": dt.date(2026, 10, 1),
        "simulation_brands": ("Lumora CRM", "Pipewise"),
    }
    base.update(kw)
    return GenerationRequest(**base)  # type: ignore[arg-type]


async def test_is_deterministic() -> None:
    p = FakeProvider("anthropic", error_rate=0, sleep=_no_sleep)
    a, b = await p.generate(request()), await p.generate(request())
    assert a.text == b.text and a.output_tokens == b.output_tokens


async def test_varies_by_day_and_provider() -> None:
    p = FakeProvider("anthropic", error_rate=0, sleep=_no_sleep)
    q = FakeProvider("openai", error_rate=0, sleep=_no_sleep)
    base = (await p.generate(request())).text
    assert base != (await p.generate(request(day=dt.date(2026, 10, 2)))).text
    assert base != (await q.generate(request())).text


async def test_answers_look_like_ranked_lists_with_sources() -> None:
    result = await FakeProvider("openai", error_rate=0, sleep=_no_sleep).generate(request())
    assert "1. **" in result.text and "https://" in result.text
    assert result.input_tokens > 0 and result.output_tokens > 0
    assert result.citations == []


async def test_perplexity_returns_out_of_band_citations() -> None:
    result = await FakeProvider("perplexity", error_rate=0, sleep=_no_sleep).generate(request())
    assert result.citations and all(c.startswith("https://") for c in result.citations)


async def test_error_injection_is_seeded_per_attempt() -> None:
    p = FakeProvider("gemini", error_rate=0.5, sleep=_no_sleep)
    outcomes = []
    for attempt in range(1, 41):
        try:
            await p.generate(request(attempt=attempt))
            outcomes.append(True)
        except ProviderError as exc:
            assert exc.retryable
            outcomes.append(False)
    assert 8 < outcomes.count(False) < 32  # roughly half fail...
    assert any(outcomes)  # ...and a retry with a new attempt number can succeed


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "perplexity"])
def test_brand_strength_is_bounded(provider: str) -> None:
    for day in range(738_000, 738_400, 7):
        assert 0.05 <= brand_strength("Lumora CRM", provider, day) <= 1.0
