"""Real vendor providers: Anthropic, OpenAI, Google Gemini and Perplexity.

Each one disables the SDK's own retries (``max_retries=0``) - the executor owns the
retry policy - and maps vendor exceptions onto :mod:`avt.providers.base` errors.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import anthropic
import httpx
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from avt.providers.base import (
    GenerationRequest,
    GenerationResult,
    PermanentProviderError,
    ProviderError,
    TransientProviderError,
    classify_http_status,
    parse_retry_after,
)

ANTHROPIC_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


def iter_urls(node: Any) -> Iterator[str]:
    """Yield every ``url``/``uri`` string found in a nested JSON-like structure.

    Vendors expose citations in different shapes (annotations, grounding chunks,
    search results); walking the dumped payload keeps each adapter tiny.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("url", "uri") and isinstance(value, str) and value.startswith("http"):
                yield value
            else:
                yield from iter_urls(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_urls(item)


def _dedupe(urls: Iterator[str]) -> list[str]:
    return list(dict.fromkeys(urls))


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, timeout: float, web_search: bool = False) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=timeout)
        self._web_search = web_search

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {}
        if self._web_search:
            kwargs["tools"] = [ANTHROPIC_WEB_SEARCH_TOOL]
        try:
            message = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_output_tokens,
                messages=[{"role": "user", "content": request.prompt}],
                # Answer like the consumer chat would, without spending on deep reasoning.
                output_config={"effort": "low"},
                # Server-side refusal fallback: a declined request is re-run on a
                # fallback model inside the same call instead of failing the task.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
                **kwargs,
            )
        except anthropic.APIStatusError as exc:
            raise classify_http_status(
                exc.status_code, str(exc), parse_retry_after(exc.response.headers.get("retry-after"))
            ) from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise TransientProviderError(str(exc)) from exc

        if message.stop_reason == "refusal":
            raise PermanentProviderError("model declined to answer (refusal)")
        text = "".join(block.text for block in message.content if block.type == "text")
        citations = _dedupe(iter_urls([b.model_dump() for b in message.content]))
        return GenerationResult(
            text=text,
            model_name=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            citations=citations,
            latency_ms=_elapsed_ms(started),
        )

    async def aclose(self) -> None:
        await self._client.close()


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, *, timeout: float, web_search: bool = False) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, max_retries=0, timeout=timeout)
        self._web_search = web_search

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        tools: list[Any] = [{"type": "web_search"}] if self._web_search else []
        try:
            response = await self._client.responses.create(
                model=request.model,
                input=request.prompt,
                max_output_tokens=request.max_output_tokens,
                tools=tools,
            )
        except openai.APIStatusError as exc:
            raise classify_http_status(
                exc.status_code, str(exc), parse_retry_after(exc.response.headers.get("retry-after"))
            ) from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise TransientProviderError(str(exc)) from exc

        usage = response.usage
        return GenerationResult(
            text=response.output_text,
            model_name=response.model,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            citations=_dedupe(iter_urls([item.model_dump() for item in response.output])),
            latency_ms=_elapsed_ms(started),
        )

    async def aclose(self) -> None:
        await self._client.close()


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, *, timeout: float, web_search: bool = False) -> None:
        self._client = genai.Client(
            api_key=api_key, http_options=genai_types.HttpOptions(timeout=int(timeout * 1000))
        )
        self._web_search = web_search

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        config = genai_types.GenerateContentConfig(
            max_output_tokens=request.max_output_tokens,
            tools=(
                [genai_types.Tool(google_search=genai_types.GoogleSearch())] if self._web_search else None
            ),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=request.model, contents=request.prompt, config=config
            )
        except genai_errors.APIError as exc:
            raise classify_http_status(exc.code, str(exc)) from exc
        except httpx.TransportError as exc:
            raise TransientProviderError(str(exc)) from exc

        usage = response.usage_metadata
        grounding = [
            c.grounding_metadata.model_dump() for c in (response.candidates or []) if c.grounding_metadata
        ]
        return GenerationResult(
            text=response.text or "",
            model_name=response.model_version or request.model,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            citations=_dedupe(iter_urls(grounding)),
            latency_ms=_elapsed_ms(started),
        )

    async def aclose(self) -> None:
        await self._client.aio.aclose()


class PerplexityProvider:
    """Perplexity's chat completions API - answers come back with web citations."""

    name = "perplexity"
    base_url = "https://api.perplexity.ai"

    def __init__(self, api_key: str, *, timeout: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                "/chat/completions",
                json={
                    "model": request.model,
                    "messages": [{"role": "user", "content": request.prompt}],
                    "max_tokens": request.max_output_tokens,
                },
            )
        except httpx.TimeoutException as exc:
            raise TransientProviderError(f"timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise TransientProviderError(str(exc)) from exc
        if resp.status_code >= 400:
            raise classify_http_status(
                resp.status_code, resp.text[:500], parse_retry_after(resp.headers.get("retry-after"))
            )
        try:
            body: dict[str, Any] = resp.json()
            text = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise ProviderError(f"unexpected perplexity payload: {exc}") from exc
        citations = [c for c in body.get("citations") or [] if isinstance(c, str)]
        citations += list(iter_urls(body.get("search_results") or []))
        usage = body.get("usage") or {}
        return GenerationResult(
            text=text,
            model_name=str(body.get("model", request.model)),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            citations=_dedupe(iter(citations)),
            latency_ms=_elapsed_ms(started),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
