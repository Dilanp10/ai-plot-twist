"""Concrete ``LLMProvider`` backed by GitHub Models (OpenAI-compatible).

Module 006 / Task T-003.

Uses the ``openai`` SDK pointed at GitHub Models'
``https://models.inference.ai.azure.com`` endpoint with a personal access
token (env ``GITHUB_MODELS_TOKEN``). Model pinned to ``gpt-4o-mini``
per SDD §2.4 / FR-003.

Critical difference vs :class:`GeminiProvider`: at 2026-06 GitHub
Models does NOT honor OpenAI's ``response_format={"type":
"json_schema", ...}``, only the looser ``json_object`` (R-001). So we:

  1. Pass ``response_format={"type": "json_object"}`` to nudge the
     model toward a JSON document.
  2. Post-validate the body against the caller's Pydantic schema
     ourselves, mapping any failure to
     :class:`LLMProviderInvalidOutput` so the router skips this
     provider without retrying.

Exception mapping mirrors the rest of the provider family — see
:mod:`app.providers.llm.gemini` for the table of equivalents:

  - ``RateLimitError`` (429)          → :class:`LLMProviderRateLimited`
  - ``APIStatusError`` (5xx)          → :class:`LLMProviderUnavailable`
  - ``APIConnectionError`` / timeout  → :class:`LLMProviderUnavailable`
  - ``APIStatusError`` (other 4xx)    → :class:`LLMProviderError` (bubbles)
  - Pydantic validation / non-JSON    → :class:`LLMProviderInvalidOutput`
"""

from __future__ import annotations

import time

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.providers.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMProviderInvalidOutput,
    LLMProviderRateLimited,
    LLMProviderUnavailable,
    LLMResponse,
)

GITHUB_MODELS_BASE_URL = "https://models.inference.ai.azure.com"


class GitHubModelsProvider(LLMProvider):
    """GitHub Models implementation of :class:`LLMProvider`.

    Parameters
    ----------
    api_key:
        Personal-access token (``GITHUB_MODELS_TOKEN``). The default
        scopes-empty classic PAT is enough — GH Models is a public
        endpoint.
    model:
        Pinned model. Default ``"gpt-4o-mini"`` per SDD §2.4 / FR-003.
    """

    name = "github_models"

    def __init__(
        self, *, api_key: str, model: str = "gpt-4o-mini"
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=GITHUB_MODELS_BASE_URL,
        )
        self._model = model

    async def health(self) -> bool:
        # No cheap reachability ping; identical reasoning as Gemini.
        return True

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        started_at = time.monotonic()
        try:
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_output_tokens,
            )
        except RateLimitError as exc:
            raise LLMProviderRateLimited(
                f"github_models rate-limited: {exc}"
            ) from exc
        except APITimeoutError as exc:
            raise LLMProviderUnavailable(
                f"github_models timeout: {exc}"
            ) from exc
        except APIConnectionError as exc:
            raise LLMProviderUnavailable(
                f"github_models connection error: {exc}"
            ) from exc
        except APIStatusError as exc:
            status = getattr(exc, "status_code", 0) or 0
            if 500 <= status < 600:
                raise LLMProviderUnavailable(
                    f"github_models server error (status={status}): {exc}"
                ) from exc
            # 4xx (auth, bad request, …) → operator-actionable: bubble.
            raise LLMProviderError(
                f"github_models client error (status={status}): {exc}"
            ) from exc

        latency_ms = int((time.monotonic() - started_at) * 1000)

        text = (completion.choices[0].message.content or "") if completion.choices else ""
        try:
            content = response_schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMProviderInvalidOutput(
                f"github_models response did not match schema "
                f"{response_schema.__name__}: {exc}"
            ) from exc
        except ValueError as exc:
            raise LLMProviderInvalidOutput(
                f"github_models response was not valid JSON for "
                f"{response_schema.__name__}: {exc}"
            ) from exc

        tokens_in, tokens_out = _extract_token_usage(completion)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self._model,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


def _extract_token_usage(completion: object) -> tuple[int, int]:
    """Return ``(tokens_in, tokens_out)`` from ``completion.usage`` or zeros.

    GitHub Models usually populates ``usage.prompt_tokens`` and
    ``usage.completion_tokens``, but some hosted models skip it. Defend
    against ``None`` to keep :class:`LLMResponse` constructable.
    """
    usage = getattr(completion, "usage", None)
    if usage is None:
        return 0, 0
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
    return tokens_in, tokens_out
