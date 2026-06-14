"""Unit tests: rerun-generation CLI (T-013).

Coverage:
  1. argparse — required --chapter-id; defaults for --api-url and
     --admin-token; explicit overrides.
  2. _resolve_admin_token — flag > env > exit 1.
  3. _run — POST to the right URL with Authorization + JSON body;
     exit 1 on HTTP >= 400 (with error body on stderr); exit 1 on
     connection failure.
  4. _print_result — emits a summary table with all expected labels.
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.scripts.rerun_generation import (
    _print_result,
    _resolve_admin_token,
    _run,
    build_parser,
)

_CHAPTER_ID = UUID("9b6f2d8a-1f30-4c11-9c5b-2d6f0a3a5e1c")
_NEW_CHAPTER_ID = UUID("c1e4f5a2-7b80-4d4e-9211-0a3b1c5d6e7f")


def parser_args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def test_parser_requires_chapter_id() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


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


def _fake_summary() -> dict[str, Any]:
    return {
        "source_chapter_id": str(_CHAPTER_ID),
        "new_chapter_id": str(_NEW_CHAPTER_ID),
        "status": "ready",
        "panels_ok": 3,
        "panels_degraded": 0,
        "duration_ms": 23456,
        "has_winner": True,
    }


def test_run_posts_to_correct_url_with_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json=_fake_summary())

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
        == "https://prod.example.com/api/v1/internal/generation/rerun"
    )
    assert captured["headers"]["authorization"] == "Bearer secret-tok"
    assert captured["headers"]["content-type"] == "application/json"
    decoded = json.loads(captured["json"])
    assert decoded == {"chapter_id": str(_CHAPTER_ID)}
    assert result["status"] == "ready"


def test_run_exits_1_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "type": "about:blank",
                "title": "Generation pipeline not configured",
                "status": 503,
                "code": "generation_pipeline_unavailable",
                "instance": "/api/v1/internal/generation/rerun",
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
    assert "generation_pipeline_unavailable" in err


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
    _print_result(_fake_summary())
    out = capsys.readouterr().out
    for label in (
        "source_chapter_id",
        "new_chapter_id",
        "status",
        "panels_ok",
        "panels_degraded",
        "duration_ms",
        "has_winner",
    ):
        assert label in out, f"missing label: {label}"
    assert str(_CHAPTER_ID) in out
    assert str(_NEW_CHAPTER_ID) in out
    assert "23456" in out  # duration_ms
    assert "Generation pipeline rerun completado" in out


def test_print_result_handles_missing_fields_gracefully(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sparse = {"source_chapter_id": str(uuid4())}
    _print_result(sparse)
    out = capsys.readouterr().out
    assert "status" in out
    assert "None" in out
