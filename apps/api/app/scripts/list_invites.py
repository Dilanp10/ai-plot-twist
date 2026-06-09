"""CLI: list-invites — show all invite codes with optional filters.

Usage (via pnpm):
    pnpm list-invites [-- OPTIONS]

Usage (direct):
    uv run python -m app.scripts.list_invites [OPTIONS]

Options:
    --status STATUS    Filter by status: unused | redeemed | revoked | expired
    --expired-only     Show only unused codes whose expiry is in the past
    --json             Output JSON array instead of table (grep-friendly)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infra.invites_repo import InviteRow, InvitesRepo
from app.settings import get_settings

_VALID_STATUSES = {"unused", "redeemed", "revoked", "expired"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="list-invites",
        description="Lista todos los códigos de invite.",
    )
    p.add_argument(
        "--status",
        choices=sorted(_VALID_STATUSES),
        default=None,
        metavar="STATUS",
        help="Filtrar por status: unused | redeemed | revoked | expired",
    )
    p.add_argument(
        "--expired-only",
        action="store_true",
        help="Mostrar solo códigos 'unused' cuya fecha de vencimiento ya pasó",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Salida JSON (para grep/jq)",
    )
    return p


async def _run(args: argparse.Namespace) -> list[InviteRow]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with factory() as session:
        rows = await InvitesRepo(session).list_all()

    await engine.dispose()

    # Apply filters (in Python — table is tiny in closed beta)
    now = datetime.now(UTC)
    if args.status:
        rows = [r for r in rows if r.status == args.status]
    if args.expired_only:
        rows = [
            r for r in rows
            if r.status == "unused" and r.expires_at.replace(tzinfo=UTC) < now
        ]

    return rows


def _print_table(rows: list[InviteRow]) -> None:
    if not rows:
        print("(sin resultados)")
        return

    headers = ["CÓDIGO", "STATUS", "VENCE", "NOTA"]
    data = [
        [
            r.code,
            r.status,
            r.expires_at.strftime("%Y-%m-%d"),
            r.note or "",
        ]
        for r in rows
    ]
    widths = [
        max(len(h), max(len(row[i]) for row in data))
        for i, h in enumerate(headers)
    ]
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    fmt = "| " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"

    print(sep)
    print(fmt.format(*headers))
    print(sep)
    for row in data:
        print(fmt.format(*row))
    print(sep)
    print(f"\n{len(rows)} resultado(s).")


def _print_json(rows: list[InviteRow]) -> None:
    payload = [
        {
            "code": r.code,
            "status": r.status,
            "issued_by": r.issued_by,
            "issued_at": r.issued_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
            "redeemed_at": r.redeemed_at.isoformat() if r.redeemed_at else None,
            "note": r.note,
        }
        for r in rows
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    args = build_parser().parse_args()
    rows = asyncio.run(_run(args))
    if args.as_json:
        _print_json(rows)
    else:
        _print_table(rows)


if __name__ == "__main__":
    main()
