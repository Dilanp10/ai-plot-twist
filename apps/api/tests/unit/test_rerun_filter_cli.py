"""Unit tests: rerun-filter CLI (T-012).

Coverage:
  1. argparse parses --chapter-id (required), defaults for --api-url
     and --admin-token.
  2. _run posts to the right URL with Authorization header + body.
  3. _print_result emits a breakdown table with all expected labels.
  4. _run exits 1 on HTTP >= 400 with the error body on stderr.
  5. _resolve_admin_token exits 1 when no flag and no env.
"""

from __future__ import annotations

import argparse
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.scripts.rerun_filter import (
    _print_result,
    _resolve_admin_token,
    _run,
    build_parser,
)

_CHAPTER_ID = UUID("216f87b5-6457-439f-8832-a9df7bba6b1e")


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def test_parser_requires_chapter_id() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_defaults() -> None:
    args = parser_args(f"--chapter-id={_CHAPTER_ID}")
    assert args.chapter_id == _CHAPTER_ID
    assert args.api_url == "http://localhost:8000"
    assert args.admin_token is None


def test_parser_accepts_overrides() -> None:
    args = parser_args(
        f"--chapter-id={_CHAPTER_ID}",
        "--api-url=https://prod.example.com",
        "--admin-token=tok-xyz",
    )
    assert args.api_url == "https://prod.example.com"
    assert args.admin_token == "tok-xyz"


def parser_args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


# ---------------------------------------------------------------------------
# _resolve_admin_token
# ---------------------------------------------------------------------------


def test_resolve_admin_token_from_flag_takes_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "env-token")
    args = parser_args(
        f"--chapter-id={_CHAPTER_ID}", "--admin-token=flag-token"
    )
    assert _resolve_admin_token(args) == "flag-token"


def test_resolve_admin_token_from_env_when_flag_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "env-token")
    args = parser_args(f"--chapter-id={_CHAPTER_ID}")
    assert _resolve_admin_token(args) == "env-token"


def test_resolve_admin_token_exits_1_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    args = parser_args(f"--chapter-id={_CHAPTER_ID}")
    with pytest.raises(SystemExit) as exc:
        _resolve_admin_token(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ADMIN_TOKEN no está configurado" in err


# ---------------------------------------------------------------------------
# _run: HTTP plumbing
# ---------------------------------------------------------------------------


def _fake_breakdown() -> dict[str, Any]:
    return {
        "chapter_id": str(_CHAPTER_ID),
        "twist_count": 5,
        "classified": 5,
        "batches": 1,
        "breakdown": {
            "approved": 3,
            "rejected_offensive": 1,
            "rejected_incoherent": 1,
            "rejected_spam": 0,
        },
        "default_denied": 0,
        "slur_overrides": 1,
        "duration_ms": 1234,
    }


def test_run_posts_to_correct_url_with_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json=_fake_breakdown())

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    monkeypatch.setenv("ADMIN_TOKEN", "secret-tok")
    args = parser_args(
        f"--chapter-id={_CHAPTER_ID}",
        "--api-url=https://prod.example.com",
    )

    result = _run(args, client=client)

    assert (
        captured["url"]
        == "https://prod.example.com/api/v1/internal/director/replay"
    )
    assert captured["headers"]["authorization"] == "Bearer secret-tok"
    assert captured["headers"]["content-type"] == "application/json"
    import json

    decoded = json.loads(captured["json"])
    assert decoded == {"chapter_id": str(_CHAPTER_ID)}
    assert result["twist_count"] == 5


def test_run_exits_1_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "type": "about:blank",
                "title": "Director router not configured",
                "status": 503,
                "code": "director_router_unavailable",
                "instance": "/api/v1/internal/director/replay",
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    args = parser_args(f"--chapter-id={_CHAPTER_ID}")

    with pytest.raises(SystemExit) as exc:
        _run(args, client=client)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "HTTP 503" in err
    assert "director_router_unavailable" in err


def test_run_exits_1_on_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    args = parser_args(
        f"--chapter-id={_CHAPTER_ID}",
        "--api-url=http://offline.example.com",
    )

    with pytest.raises(SystemExit) as exc:
        _run(args, client=client)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "fallo de red" in err
    assert "offline.example.com" in err


# ---------------------------------------------------------------------------
# _print_result formatting
# ---------------------------------------------------------------------------


def test_print_result_emits_all_labels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_result(_fake_breakdown())
    out = capsys.readouterr().out
    for label in (
        "twist_count",
        "classified",
        "batches",
        "approved",
        "rejected_offensive",
        "rejected_incoherent",
        "rejected_spam",
        "default_denied",
        "slur_overrides",
        "duration_ms",
    ):
        assert label in out, f"missing label: {label}"
    assert str(_CHAPTER_ID) in out
    assert "1234" in out  # duration_ms
    assert "Director filter replay completado" in out


def test_print_result_handles_missing_fields_gracefully(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Endpoint hypothetically returns sparse payload — labels appear with None."""
    sparse = {"chapter_id": str(uuid4())}
    _print_result(sparse)
    out = capsys.readouterr().out
    # The labels are still printed; values are None (str(None) == "None").
    assert "approved" in out
    assert "None" in out
