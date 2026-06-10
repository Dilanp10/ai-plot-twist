"""Unit tests: kill_switch CLI script.

Module 003 / Task T-021.

Uses an injected ``httpx.Client`` mock so no real HTTP request is made.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import httpx
import pytest

from app.scripts.kill_switch import _run, build_parser

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, json_body: dict) -> httpx.Response:  # type: ignore[type-arg]
    """Build a fake httpx.Response."""
    import json

    return httpx.Response(
        status_code=status_code,
        content=json.dumps(json_body).encode(),
        headers={"Content-Type": "application/json"},
    )


def _args(
    *,
    on: bool,
    reason: str | None = None,
    api_url: str = "http://localhost:8000",
) -> argparse.Namespace:
    return argparse.Namespace(on=on, reason=reason, api_url=api_url)


# ---------------------------------------------------------------------------
# Happy path — turn on
# ---------------------------------------------------------------------------


def test_turn_on_posts_correct_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    monkeypatch.setenv("JWT_SECRET", "test-jwt")

    from app.settings import get_settings
    get_settings.cache_clear()

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = _mock_response(
        200, {"status": "kill_switch_active", "reason": "test reason"}
    )

    result = _run(_args(on=True, reason="test reason"), client=mock_client)

    assert result["status"] == "kill_switch_active"
    call_kwargs = mock_client.post.call_args
    assert call_kwargs.kwargs["json"] == {"on": True, "reason": "test reason"}
    assert "Bearer test-admin" in call_kwargs.kwargs["headers"]["Authorization"]
    assert "kill-switch" in call_kwargs.args[0]
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Happy path — turn off
# ---------------------------------------------------------------------------


def test_turn_off_posts_on_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    monkeypatch.setenv("JWT_SECRET", "test-jwt")

    from app.settings import get_settings
    get_settings.cache_clear()

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = _mock_response(
        200, {"status": "kill_switch_inactive", "reason": None}
    )

    result = _run(_args(on=False), client=mock_client)

    assert result["status"] == "kill_switch_inactive"
    assert mock_client.post.call_args.kwargs["json"]["on"] is False
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# ADMIN_TOKEN missing → sys.exit(1)
# ---------------------------------------------------------------------------


def test_missing_admin_token_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    monkeypatch.setenv("JWT_SECRET", "test-jwt")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    from app.settings import get_settings
    get_settings.cache_clear()

    with pytest.raises(SystemExit) as exc:
        _run(_args(on=True))
    assert exc.value.code == 1
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# HTTP error → sys.exit(1)
# ---------------------------------------------------------------------------


def test_http_403_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    monkeypatch.setenv("JWT_SECRET", "test-jwt")

    from app.settings import get_settings
    get_settings.cache_clear()

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = _mock_response(
        403, {"code": "bad_admin_token", "status": 403}
    )

    with pytest.raises(SystemExit) as exc:
        _run(_args(on=True), client=mock_client)
    assert exc.value.code == 1
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Parser: --on/--off mutual exclusion
# ---------------------------------------------------------------------------


def test_parser_rejects_both_flags() -> None:
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--on", "--off"])


def test_parser_rejects_neither_flag() -> None:
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args([])


def test_parser_on_sets_true() -> None:
    p = build_parser()
    args = p.parse_args(["--on", "--reason", "rebuild"])
    assert args.on is True
    assert args.reason == "rebuild"


def test_parser_off_sets_false() -> None:
    p = build_parser()
    args = p.parse_args(["--off"])
    assert args.on is False


# ---------------------------------------------------------------------------
# api-url override
# ---------------------------------------------------------------------------


def test_custom_api_url_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    monkeypatch.setenv("JWT_SECRET", "test-jwt")

    from app.settings import get_settings
    get_settings.cache_clear()

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = _mock_response(
        200, {"status": "kill_switch_inactive", "reason": None}
    )

    _run(
        argparse.Namespace(on=False, reason=None, api_url="http://staging:9000"),
        client=mock_client,
    )

    url_called = mock_client.post.call_args.args[0]
    assert url_called.startswith("http://staging:9000")
    get_settings.cache_clear()
