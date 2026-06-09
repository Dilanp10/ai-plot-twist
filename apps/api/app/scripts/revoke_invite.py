"""CLI: revoke-invite — mark an invite code as revoked.

Usage (via pnpm):
    pnpm revoke-invite [-- CODE]

Usage (direct):
    uv run python -m app.scripts.revoke_invite CODE [--allow-prod]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.invites import InviteCode
from app.infra.invites_repo import InvitesRepo
from app.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="revoke-invite",
        description="Revoca un código de invite (lo marca como 'revoked').",
    )
    p.add_argument("code", metavar="CODE", help="Código a revocar (ej: ABCD-EFGH)")
    p.add_argument("--allow-prod", action="store_true",
                   help="Requerido cuando ENV=prod")
    return p


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()

    if settings.env == "prod" and not args.allow_prod:
        print(
            "ERROR: Estás en ENV=prod. Pasá --allow-prod para confirmar.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        code = InviteCode.parse(args.code)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with factory() as session:
        repo = InvitesRepo(session)
        row = await repo.get_for_update(code)
        if row is None:
            print(f"ERROR: No existe ningún invite con código '{code}'.", file=sys.stderr)
            await engine.dispose()
            sys.exit(1)
        if row.status in ("revoked", "redeemed"):
            print(
                f"AVISO: El código '{code}' ya tiene status='{row.status}'. "
                "No se hizo ningún cambio.",
                file=sys.stderr,
            )
            await engine.dispose()
            sys.exit(0)
        await repo.revoke(code)
        await session.commit()

    await engine.dispose()
    print(f"✓ Código '{code}' revocado con éxito.")


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
