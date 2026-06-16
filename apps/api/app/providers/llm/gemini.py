"""Concrete ``LLMProvider`` backed by Google's Gemini SDK.

Module 006 / Task T-002.

Wraps :class:`google.genai.Client` (the ``google-genai`` SDK, NOT the
older ``google-generativeai``) with the async surface
``client.aio.models.generate_content``. Forces JSON-mode by passing
``response_mime_type='application/json'`` and the caller's Pydantic
``response_schema`` directly in :class:`google.genai.types.GenerateContentConfig`
(FR-002).

Exception mapping (the only logic the router branches on — see
:mod:`app.providers.llm.router`):

  - ``ClientError(429)``                  → :class:`LLMProviderRateLimited`
  - ``ServerError`` (5xx)                  → :class:`LLMProviderUnavailable`
  - Other ``ClientError`` (auth, 400 …)    → :class:`LLMProviderError` (bubbles)
  - Network / transport errors             → :class:`LLMProviderUnavailable`
  - Pydantic validation on the response    → :class:`LLMProviderInvalidOutput`

``health()`` returns ``True`` unconditionally — Gemini exposes no cheap
reachability endpoint, and a successful ``chat_json`` is the only true
signal. The router still benefits from the typed exceptions above.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from app.providers.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMProviderInvalidOutput,
    LLMProviderRateLimited,
    LLMProviderUnavailable,
    LLMResponse,
)


class GeminiProvider(LLMProvider):
    """Gemini implementation of :class:`LLMProvider`.

    Parameters
    ----------
    api_key:
        Google AI Studio key (``GEMINI_API_KEY`` env var in production).
    model:
        Pinned model string. Defaults to ``"gemini-2.0-flash"`` per
        SDD §2.4 / FR-002. Override only when migrating.
    """

    name = "gemini"

    def __init__(self, *, api_key: str, model: str = "gemini-1.5-flash") -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def health(self) -> bool:
        # No cheap reachability ping; assume up. Router still gets honest
        # signals through typed exceptions if the next chat_json fails.
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
        # Gemini's structured-output endpoint rejects schemas containing
        # `additionalProperties` (Pydantic emits this when extra="forbid"),
        # the JSON-Schema-only `title` field, and `$ref`/`$defs` (Gemini
        # does not resolve refs). Inline refs and strip the rest.
        raw_schema = response_schema.model_json_schema()
        defs = raw_schema.pop("$defs", {})

        def _inline_and_strip(obj: object) -> object:
            if isinstance(obj, dict):
                if "$ref" in obj:
                    ref = obj["$ref"]
                    # Refs look like "#/$defs/Foo".
                    name = ref.rsplit("/", 1)[-1]
                    target = defs.get(name, {})
                    return _inline_and_strip(target)
                cleaned: dict[str, object] = {}
                for k, v in obj.items():
                    if k in ("additionalProperties", "$defs"):
                        continue
                    # `title` at the schema level is JSON-Schema metadata
                    # that Gemini rejects. But `title` can ALSO appear as
                    # a key inside `properties` (when the Pydantic model
                    # has a field called `title`). Tell them apart by the
                    # value type: metadata-title is a string; property-
                    # definition is a dict.
                    if k == "title" and not isinstance(v, dict):
                        continue
                    cleaned[k] = _inline_and_strip(v)
                return cleaned
            if isinstance(obj, list):
                return [_inline_and_strip(x) for x in obj]
            return obj

        cleaned_schema = _inline_and_strip(raw_schema)
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=cleaned_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        started_at = time.monotonic()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=user,
                config=config,
            )
        except genai_errors.ClientError as exc:
            status = _extract_status(exc)
            # Log the full exception body so we can debug spec drift /
            # auth issues without re-deploying just to add prints.
            logger.warning(
                "gemini_client_error status=%s body=%s",
                status,
                str(exc)[:1000],
            )
            if status == 429:
                raise LLMProviderRateLimited(
                    f"gemini rate-limited (status={status}): {exc}"
                ) from exc
            # 4xx auth / bad-request → operator-actionable: bubble.
            raise LLMProviderError(
                f"gemini client error (status={status}): {exc}"
            ) from exc
        except genai_errors.ServerError as exc:
            status = _extract_status(exc)
            raise LLMProviderUnavailable(
                f"gemini server error (status={status}): {exc}"
            ) from exc
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise LLMProviderUnavailable(
                f"gemini transport error: {exc}"
            ) from exc
        except genai_errors.APIError as exc:
            # Anything else from the SDK that escaped Client/Server.
            status = _extract_status(exc)
            raise LLMProviderError(
                f"gemini api error (status={status}): {exc}"
            ) from exc

        latency_ms = int((time.monotonic() - started_at) * 1000)

        text = response.text or ""
        try:
            content = response_schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMProviderInvalidOutput(
                f"gemini response did not match schema "
                f"{response_schema.__name__}: {exc}"
            ) from exc
        except ValueError as exc:
            # Pydantic raises ValueError for non-JSON / empty body too.
            raise LLMProviderInvalidOutput(
                f"gemini response was not valid JSON for "
                f"{response_schema.__name__}: {exc}"
            ) from exc

        tokens_in, tokens_out = _extract_token_usage(response)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self._model,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_status(exc: genai_errors.APIError) -> int:
    """Best-effort extraction of the HTTP status from a Gemini ``APIError``.

    The SDK stores it on ``.code`` (preferred) and sometimes on a
    ``status`` string ("RESOURCE_EXHAUSTED", "UNAVAILABLE", …). We only
    branch on the numeric code, so the string is unused.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    return 0


def _extract_token_usage(
    response: genai_types.GenerateContentResponse,
) -> tuple[int, int]:
    """Return ``(tokens_in, tokens_out)`` from ``usage_metadata`` or ``(0, 0)``.

    Some models do not populate ``usage_metadata``; default to zero so
    :class:`LLMResponse` constructs cleanly.
    """
    usage = response.usage_metadata
    if usage is None:
        return 0, 0
    tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
    tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
    return tokens_in, tokens_out
