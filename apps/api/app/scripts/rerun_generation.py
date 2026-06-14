"""CLI: rerun-generation — regenerate the chapter following ``--chapter-id``.

Module 008 / Task T-013.

Wraps ``POST /api/v1/internal/generation/rerun`` (T-012) so the PO can
trigger a regeneration from the terminal:

    pnpm rerun-generation -- --chapter-id <UUID>

Direct invocation::

    uv run python -m app.scripts.rerun_generation --chapter-id <UUID>

Options:
    --chapter-id UUID      SOURCE chapter ``public_id`` (required).
    --api-url URL          API base URL (default: http://localhost:8000).
    --admin-token TOKEN    Bearer token; falls back to env ``ADMIN_TOKEN``.

Exit codes:
    0 — rerun completed; summary printed to stdout.
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rerun-generation",
        description=(
            "Regenera el capítulo que sigue al --chapter-id provisto a "
            "través del endpoint admin del pipeline de generación (T-012)."
        ),
    )
    p.add_argument(
        "--chapter-id",
        required=True,
        type=UUID,
        metavar="UUID",
        help="public_id del capítulo SOURCE (UUID).",
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


def _resolve_admin_token(args: argparse.Namespace) -> str:
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
    token = _resolve_admin_token(args)
    url = f"{args.api_url.rstrip('/')}/api/v1/internal/generation/rerun"
    body = {"chapter_id": str(args.chapter_id)}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # The generation pipeline can take minutes (image rendering + TTS),
    # so the CLI timeout is generous — slightly above the server-side
    # GENERATION_DEADLINE_S default (600 s).
    _client = client or httpx.Client(timeout=900.0)
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


def _print_result(result: dict[str, Any]) -> None:
    rows: list[tuple[str, Any]] = [
        ("source_chapter_id", result.get("source_chapter_id")),
        ("new_chapter_id", result.get("new_chapter_id")),
        ("status", result.get("status")),
        ("panels_ok", result.get("panels_ok")),
        ("panels_degraded", result.get("panels_degraded")),
        ("duration_ms", result.get("duration_ms")),
        ("has_winner", result.get("has_winner")),
    ]
    width = max(len(label) for label, _ in rows)
    print("\nGeneration pipeline rerun completado:")
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")


def main() -> None:
    args = build_parser().parse_args()
    result = _run(args)
    _print_result(result)


if __name__ == "__main__":
    main()
