"""CLI: issue-invite — mint N new invite codes and print a table.

Usage (via pnpm):
    pnpm issue-invite [-- OPTIONS]

Usage (direct):
    uv run python -m app.scripts.issue_invite [OPTIONS]

Options:
    --count N             Codes to generate (default: 1)
    --ttl-days D          Days until expiry (default: 30)
    --note TEXT           Human note attached to every code
    --display-name-hint T Hint for the intended recipient (appended to issued_by)
    --allow-prod          Required when ENV=prod (safety gate)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.invites import InviteCode
from app.infra.invites_repo import InviteRow, InvitesRepo
from app.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="issue-invite",
        description="Mint nuevos códigos de invite para AI Plot Twist.",
    )
    p.add_argument("--count", type=int, default=1, metavar="N",
                   help="Cantidad de códigos a generar (default: 1)")
    p.add_argument("--ttl-days", type=int, default=30, metavar="D",
                   help="Días de vigencia (default: 30)")
    p.add_argument("--note", default=None, metavar="TEXT",
                   help="Nota opcional para todos los códigos")
    p.add_argument("--display-name-hint", default=None, metavar="TEXT",
                   help="Sugerencia de nombre del destinatario")
    p.add_argument("--allow-prod", action="store_true",
                   help="Requerido cuando ENV=prod")
    return p


async def _run(args: argparse.Namespace) -> list[InviteRow]:
    """Core logic — testable independently of ``sys.argv``."""
    settings = get_settings()

    if settings.env == "prod" and not args.allow_prod:
        print(
            "ERROR: Estás en ENV=prod. Pasá --allow-prod para confirmar.",
            file=sys.stderr,
        )
        sys.exit(1)

    issued_by = "cli"
    if args.display_name_hint:
        issued_by = f"cli ({args.display_name_hint})"

    expires_at = datetime.now(UTC) + timedelta(days=args.ttl_days)

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    rows: list[InviteRow] = []
    async with factory() as session:
        repo = InvitesRepo(session)
        for _ in range(args.count):
            row = await repo.insert(
                code=InviteCode.generate(),
                expires_at=expires_at,
                issued_by=issued_by,
                note=args.note,
            )
            rows.append(row)
        await session.commit()

    await engine.dispose()
    return rows


def _print_table(rows: list[InviteRow]) -> None:
    headers = ["CÓDIGO", "VENCE", "NOTA"]
    data = [
        [r.code, r.expires_at.strftime("%Y-%m-%d"), r.note or ""]
        for r in rows
    ]
    widths = [
        max(len(h), max((len(row[i]) for row in data), default=0))
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
    print(f"\n{len(rows)} código(s) generado(s) con éxito.")


def main() -> None:
    args = build_parser().parse_args()
    rows = asyncio.run(_run(args))
    _print_table(rows)


if __name__ == "__main__":
    main()
