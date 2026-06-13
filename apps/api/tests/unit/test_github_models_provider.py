"""Unit tests: GitHubModelsProvider (T-003).

Mocks ``client.chat.completions.create`` (the only SDK call the
provider makes) and exercises every code path documented in the
provider's docstring.

  1. Happy path returns a parsed Pydantic instance + token usage.
  2. RateLimitError → LLMProviderRateLimited.
  3. APIStatusError 5xx → LLMProviderUnavailable.
  4. APIStatusError 4xx (auth) → LLMProviderError (no failover).
  5. APIConnectionError → LLMProviderUnavailable.
  6. APITimeoutError → LLMProviderUnavailable.
  7. Empty / non-JSON message content → LLMProviderInvalidOutput.
  8. JSON missing required field → LLMProviderInvalidOutput.
  9. Missing usage block defaults to (0, 0).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import BaseModel

from app.providers.llm.base import (
    LLMProviderError,
    LLMProviderInvalidOutput,
    LLMProviderRateLimited,
    LLMProviderUnavailable,
)
from app.providers.llm.github_models import (
    GITHUB_MODELS_BASE_URL,
    GitHubModelsProvider,
)


class _Verdict(BaseModel):
    accept: bool
    reason: str


def _completion(
    text: str,
    *,
    tokens_in: int | None = 12,
    tokens_out: int | None = 7,
) -> MagicMock:
    """Build a stand-in for ``openai.types.chat.ChatCompletion``."""
    completion = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    completion.choices = [choice]
    if tokens_in is None and tokens_out is None:
        completion.usage = None
    else:
        usage = MagicMock()
        usage.prompt_tokens = tokens_in
        usage.completion_tokens = tokens_out
        completion.usage = usage
    return completion


def _patched_provider() -> tuple[GitHubModelsProvider, AsyncMock]:
    provider = GitHubModelsProvider(api_key="dummy", model="gh-test")
    fake_call = AsyncMock()
    provider._client.chat.completions.create = fake_call  # type: ignore[method-assign]
    return provider, fake_call


def _http_response(status: int) -> httpx.Response:
    """Minimal real httpx.Response — openai exceptions need one."""
    return httpx.Response(
        status_code=status,
        request=httpx.Request("POST", "https://x.example/chat"),
    )


def _http_request() -> httpx.Request:
    return httpx.Request("POST", "https://x.example/chat")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_github_models_happy_path() -> None:
    provider, fake_call = _patched_provider()
    fake_call.return_value = _completion('{"accept": true, "reason": "ok"}')

    resp = await provider.chat_json(
        system="sys", user="usr", response_schema=_Verdict
    )

    assert isinstance(resp.content, _Verdict)
    assert resp.content.accept is True
    assert resp.content.reason == "ok"
    assert resp.provider == "github_models"
    assert resp.model == "gh-test"
    assert resp.tokens_in == 12
    assert resp.tokens_out == 7

    fake_call.assert_awaited_once()
    assert fake_call.await_args is not None
    kwargs = fake_call.await_args.kwargs
    assert kwargs["model"] == "gh-test"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


async def test_github_models_rate_limit_is_rate_limited() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = RateLimitError(
        "rate limited",
        response=_http_response(429),
        body=None,
    )

    with pytest.raises(LLMProviderRateLimited, match="rate-limited"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_github_models_5xx_is_unavailable() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = APIStatusError(
        "boom",
        response=_http_response(503),
        body=None,
    )

    with pytest.raises(LLMProviderUnavailable, match="503"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_github_models_auth_4xx_bubbles_as_generic() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = APIStatusError(
        "unauthorized",
        response=_http_response(401),
        body=None,
    )

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )
    # The router branches only on the three failover subclasses.
    assert not isinstance(exc_info.value, LLMProviderRateLimited)
    assert not isinstance(exc_info.value, LLMProviderUnavailable)
    assert not isinstance(exc_info.value, LLMProviderInvalidOutput)


async def test_github_models_connection_error_is_unavailable() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = APIConnectionError(
        message="dns lookup failed",
        request=_http_request(),
    )

    with pytest.raises(LLMProviderUnavailable, match="connection"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_github_models_timeout_is_unavailable() -> None:
    provider, fake_call = _patched_provider()
    fake_call.side_effect = APITimeoutError(request=_http_request())

    with pytest.raises(LLMProviderUnavailable, match="timeout"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


# ---------------------------------------------------------------------------
# Invalid output
# ---------------------------------------------------------------------------


async def test_github_models_non_json_is_invalid_output() -> None:
    provider, fake_call = _patched_provider()
    fake_call.return_value = _completion("not even close to json")

    with pytest.raises(LLMProviderInvalidOutput, match=_Verdict.__name__):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_github_models_schema_mismatch_is_invalid_output() -> None:
    provider, fake_call = _patched_provider()
    fake_call.return_value = _completion('{"reason": "missing accept"}')

    with pytest.raises(LLMProviderInvalidOutput, match=_Verdict.__name__):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


# ---------------------------------------------------------------------------
# Token usage + constructor
# ---------------------------------------------------------------------------


async def test_github_models_missing_usage_defaults_to_zero() -> None:
    provider, fake_call = _patched_provider()
    fake_call.return_value = _completion(
        '{"accept": true, "reason": "ok"}',
        tokens_in=None,
        tokens_out=None,
    )

    resp = await provider.chat_json(
        system="s", user="u", response_schema=_Verdict
    )
    assert resp.tokens_in == 0
    assert resp.tokens_out == 0


def test_github_models_default_model_is_pinned() -> None:
    p = GitHubModelsProvider(api_key="dummy")
    assert p._model == "gpt-4o-mini"


def test_github_models_constructor_points_to_gh_endpoint() -> None:
    with patch("app.providers.llm.github_models.AsyncOpenAI") as mock_cls:
        GitHubModelsProvider(api_key="ghp_abc")
    mock_cls.assert_called_once_with(
        api_key="ghp_abc",
        base_url=GITHUB_MODELS_BASE_URL,
    )
