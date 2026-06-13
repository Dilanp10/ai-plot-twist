"""CLI: rerun-filter — replay the director's filter for a chapter.

Module 006 / Task T-012.

Wraps ``POST /api/v1/internal/director/replay`` (T-011) so the PO can
trigger a re-classification from the terminal:

    pnpm rerun-filter -- --chapter-id <UUID>

Direct invocation::

    uv run python -m app.scripts.rerun_filter --chapter-id <UUID>

Options:
    --chapter-id UUID      Chapter ``public_id`` to replay (required).
    --api-url URL          API base URL (default: http://localhost:8000).
    --admin-token TOKEN    Bearer token; falls back to env ``ADMIN_TOKEN``.

Exit codes:
    0 — replay completed; breakdown printed to stdout.
    1 — missing ``ADMIN_TOKEN``, HTTP error, or connection failure.
    2 — argparse usage error.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any
from uuid import UUID

import httpx

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rerun-filter",
        description=(
            "Re-clasifica todos los twists de un capítulo a través del "
            "endpoint admin del director (T-011)."
        ),
    )
    p.add_argument(
        "--chapter-id",
        required=True,
        type=UUID,
        metavar="UUID",
        help="public_id del capítulo (UUID).",
    )
    p.add_argument(
        "--api-url",
        default="http://localhost:8000",
        metavar="URL",
        help="URL base del API (default: http://localhost:8000)",
    )
    p.add_argument(
        "--admin-token",
        default=None,
        metavar="TOKEN",
        help=(
            "Bearer admin token. Si se omite, se lee la variable de "
            "entorno ADMIN_TOKEN."
        ),
    )
    return p


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _resolve_admin_token(args: argparse.Namespace) -> str:
    """Return the token from --admin-token or env; exit 1 if both missing."""
    if args.admin_token:
        return str(args.admin_token)
    env_token = os.environ.get("ADMIN_TOKEN", "")
    if env_token:
        return env_token
    print(
        "ERROR: ADMIN_TOKEN no está configurado. Pasalo con "
        "--admin-token <TOKEN> o exportá la variable de entorno.",
        file=sys.stderr,
    )
    sys.exit(1)


def _run(
    args: argparse.Namespace,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Post the replay request; return the parsed JSON or exit 1.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    client:
        Optional pre-built ``httpx.Client`` (injected by tests).
    """
    token = _resolve_admin_token(args)
    url = f"{args.api_url.rstrip('/')}/api/v1/internal/director/replay"
    body = {"chapter_id": str(args.chapter_id)}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    _client = client or httpx.Client(timeout=300.0)
    close_after = client is None

    try:
        try:
            r = _client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            print(
                f"ERROR: fallo de red contactando {url}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
    finally:
        if close_after:
            _client.close()

    if r.status_code >= 400:
        print(
            f"ERROR: HTTP {r.status_code} desde {url}\n{r.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    return dict(r.json())


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_result(result: dict[str, Any]) -> None:
    breakdown = result.get("breakdown", {})
    chapter_id = result.get("chapter_id", "?")
    rows: list[tuple[str, Any]] = [
        ("twist_count", result.get("twist_count")),
        ("classified", result.get("classified")),
        ("batches", result.get("batches")),
        ("approved", breakdown.get("approved")),
        ("rejected_offensive", breakdown.get("rejected_offensive")),
        ("rejected_incoherent", breakdown.get("rejected_incoherent")),
        ("rejected_spam", breakdown.get("rejected_spam")),
        ("default_denied", result.get("default_denied")),
        ("slur_overrides", result.get("slur_overrides")),
        ("duration_ms", result.get("duration_ms")),
    ]
    width = max(len(label) for label, _ in rows)
    print(f"\nDirector filter replay completado para {chapter_id}:")
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = build_parser().parse_args()
    result = _run(args)
    _print_result(result)


if __name__ == "__main__":
    main()
