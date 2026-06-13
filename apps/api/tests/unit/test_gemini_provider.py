"""Unit tests: GeminiProvider (T-002).

The provider talks to ``client.aio.models.generate_content``. We mock
that single coroutine and exercise every code path:

  1. Happy path returns a parsed Pydantic instance + token usage.
  2. ClientError(429) → LLMProviderRateLimited.
  3. ServerError(503) → LLMProviderUnavailable.
  4. ClientError(401) → LLMProviderError (auth bubble, no failover).
  5. httpx.TransportError → LLMProviderUnavailable.
  6. Empty / non-JSON response → LLMProviderInvalidOutput.
  7. Token usage is sourced from response.usage_metadata.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel

from app.providers.llm.base import (
    LLMProviderError,
    LLMProviderInvalidOutput,
    LLMProviderRateLimited,
    LLMProviderUnavailable,
)
from app.providers.llm.gemini import GeminiProvider


class _Verdict(BaseModel):
    accept: bool
    reason: str


def _fake_response(
    text: str,
    *,
    tokens_in: int | None = 10,
    tokens_out: int | None = 5,
) -> MagicMock:
    """Build a stand-in for ``google.genai.types.GenerateContentResponse``."""
    response = MagicMock()
    response.text = text
    if tokens_in is None and tokens_out is None:
        response.usage_metadata = None
    else:
        usage = MagicMock()
        usage.prompt_token_count = tokens_in
        usage.candidates_token_count = tokens_out
        response.usage_metadata = usage
    return response


def _patched_provider() -> tuple[GeminiProvider, AsyncMock]:
    """Build a GeminiProvider and return it together with the AsyncMock
    that stands in for the actual SDK call."""
    provider = GeminiProvider(api_key="dummy", model="gemini-test")
    fake_call = AsyncMock()
    provider._client.aio.models.generate_content = fake_call  # type: ignore[method-assign]
    return provider, fake_call


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_gemini_happy_path_returns_parsed_response() -> None:
    provider, fake_call = _patched_provider()
    fake_call.return_value = _fake_response('{"accept": true, "reason": "ok"}')

    resp = await provider.chat_json(
        system="sys", user="usr", response_schema=_Verdict
    )

    assert isinstance(resp.content, _Verdict)
    assert resp.content.accept is True
    assert resp.content.reason == "ok"
    assert resp.provider == "gemini"
    assert resp.model == "gemini-test"
    assert resp.tokens_in == 10
    assert resp.tokens_out == 5
    fake_call.assert_awaited_once()
    # The SDK was invoked with the system prompt in the config and the
    # user prompt as positional contents.
    assert fake_call.await_args is not None
    kwargs = fake_call.await_args.kwargs
    assert kwargs["model"] == "gemini-test"
    assert kwargs["contents"] == "usr"
    assert kwargs["config"].system_instruction == "sys"
    assert kwargs["config"].response_schema is _Verdict


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


def _client_error(code: int) -> genai_errors.ClientError:
    """Build a ClientError. The SDK signature takes (code, response_json[, response])."""
    return genai_errors.ClientError(code, {"error": {"message": f"http {code}"}})


def _server_error(code: int) -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"error": {"message": f"http {code}"}})


async def test_gemini_rate_limit_429_is_rate_limited() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = _client_error(429)

    with pytest.raises(LLMProviderRateLimited, match="429"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_gemini_5xx_server_error_is_unavailable() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = _server_error(503)

    with pytest.raises(LLMProviderUnavailable, match="503"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_gemini_auth_4xx_bubbles_as_generic_error() -> None:
    """A 401/403 from the SDK must NOT failover — operator action needed."""
    provider, fake_call = _patched_provider()
    fake_call.side_effect = _client_error(401)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )
    # The router uses isinstance(exc, LLMProviderRateLimited|Unavailable|InvalidOutput)
    # for failover; a raw LLMProviderError must not be any of those subtypes.
    assert not isinstance(exc_info.value, LLMProviderRateLimited)
    assert not isinstance(exc_info.value, LLMProviderUnavailable)
    assert not isinstance(exc_info.value, LLMProviderInvalidOutput)


async def test_gemini_transport_error_is_unavailable() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = httpx.ConnectError("dns lookup failed")

    with pytest.raises(LLMProviderUnavailable, match="dns lookup failed"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


# ---------------------------------------------------------------------------
# Invalid output
# ---------------------------------------------------------------------------


async def test_gemini_non_json_response_is_invalid_output() -> None:
    provider, fake_call = _patched_provider()
    fake_call.return_value = _fake_response("this is not json at all")

    with pytest.raises(LLMProviderInvalidOutput, match=_Verdict.__name__):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_gemini_schema_mismatch_is_invalid_output() -> None:
    provider, fake_call = _patched_provider()
    # Valid JSON but missing required field.
    fake_call.return_value = _fake_response('{"reason": "missing accept"}')

    with pytest.raises(LLMProviderInvalidOutput, match=_Verdict.__name__):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------


async def test_gemini_missing_usage_metadata_defaults_to_zero() -> None:
    provider, fake_call = _patched_provider()
    fake_call.return_value = _fake_response(
        '{"accept": true, "reason": "ok"}',
        tokens_in=None,
        tokens_out=None,
    )

    resp = await provider.chat_json(
        system="s", user="u", response_schema=_Verdict
    )
    assert resp.tokens_in == 0
    assert resp.tokens_out == 0


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_gemini_default_model_is_pinned() -> None:
    """FR-002 + SDD §2.4: the pin lives in code; bumps are a git change."""
    p = GeminiProvider(api_key="dummy")
    assert p._model == "gemini-2.0-flash"


def test_gemini_constructor_creates_real_client_with_api_key() -> None:
    """We delegate api-key handling to the SDK — just confirm it lands."""
    with patch("app.providers.llm.gemini.genai.Client") as mock_cls:
        GeminiProvider(api_key="abc-123")
    mock_cls.assert_called_once_with(api_key="abc-123")
    _ = MagicMock  # keep imports used
    _ = Any  # silence unused import in CI strict mode
