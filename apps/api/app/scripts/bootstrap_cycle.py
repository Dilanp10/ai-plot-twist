"""CLI: bootstrap-cycle — seed a new season + day-0 chapter + first cycle.

Module 003 / Task T-019.

Usage (via pnpm):
    pnpm bootstrap-cycle -- --season s01 --day-zero-manifest docs/seed/example-cap0.yaml

Usage (direct):
    uv run python -m app.scripts.bootstrap_cycle --season SLUG --day-zero-manifest FILE.yaml

Options:
    --season SLUG              Season slug (e.g. "s01").  Must match YAML ``slug``.
    --day-zero-manifest FILE   Path to YAML manifest file.
    --force-replace            Deactivate any existing active season before inserting.

YAML manifest schema (all required unless noted):
    slug: str
    title: str
    started_on: "YYYY-MM-DD"   # optional; defaults to today ART (UTC-3)
    bible:                     # optional; stored as season.bible_json
      ...
    chapter:
      title: str
      synopsis: str
      manifest:                # stored as chapter.manifest_json
        ...

Exits 0 on success, 1 on any error.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.cycle_clock import to_art
from app.infra.chapters_repo import ChaptersRepo
from app.infra.cycles_repo import CyclesRepo
from app.infra.seasons_repo import SeasonsRepo
from app.settings import get_settings

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapResult:
    """Identifiers for the newly created rows."""

    season_id: int
    chapter_id: int
    cycle_id: int
    slug: str
    cycle_date: date


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bootstrap-cycle",
        description=(
            "Crea temporada + capítulo 0 + primer ciclo para AI Plot Twist."
        ),
    )
    p.add_argument(
        "--season",
        required=True,
        metavar="SLUG",
        help="Slug de la temporada (ej. 's01').",
    )
    p.add_argument(
        "--day-zero-manifest",
        required=True,
        metavar="FILE",
        help="Ruta al archivo YAML con los datos iniciales.",
    )
    p.add_argument(
        "--force-replace",
        action="store_true",
        help="Desactiva la temporada activa actual antes de insertar.",
    )
    return p


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require(data: dict[str, Any], *keys: str, context: str = "") -> None:
    """Exit 1 if any of *keys* is missing from *data*."""
    prefix = f"{context}." if context else ""
    for key in keys:
        if key not in data:
            print(
                f"ERROR: Campo requerido '{prefix}{key}' no encontrado en el YAML.",
                file=sys.stderr,
            )
            sys.exit(1)


# ---------------------------------------------------------------------------
# Core logic (async, importable for tests)
# ---------------------------------------------------------------------------


async def _run(
    args: argparse.Namespace,
    *,
    database_url: str | None = None,
) -> BootstrapResult:
    """Bootstrap a season/chapter/cycle from a YAML manifest.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    database_url:
        Override DB URL (used by tests to inject the test database).
        Defaults to ``get_settings().database_url``.
    """
    settings = get_settings()
    db_url = database_url or settings.database_url

    # ── 1. Load YAML ─────────────────────────────────────────────────────
    manifest_path = Path(args.day_zero_manifest)
    if not manifest_path.exists():
        print(f"ERROR: Archivo no encontrado: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    data: dict[str, Any] = yaml.safe_load(
        manifest_path.read_text(encoding="utf-8")
    ) or {}

    # ── 2. Validate YAML ──────────────────────────────────────────────────
    _require(data, "slug", "title", "chapter")
    _require(data["chapter"], "title", "synopsis", "manifest", context="chapter")

    if data["slug"] != args.season:
        print(
            f"ERROR: El slug del YAML ('{data['slug']}') no coincide con "
            f"--season '{args.season}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── 3. Resolve dates ─────────────────────────────────────────────────
    now_art = to_art(datetime.now(UTC))
    if "started_on" in data:
        started_on = date.fromisoformat(str(data["started_on"]))
    else:
        started_on = now_art.date()
    cycle_date = now_art.date()

    bible_json: dict[str, Any] = dict(data.get("bible") or {})
    chapter_manifest: dict[str, Any] = dict(data["chapter"]["manifest"] or {})

    # ── 4. DB operations ─────────────────────────────────────────────────
    engine = create_async_engine(db_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    try:
        async with factory() as session:
            seasons = SeasonsRepo(session)
            chapters = ChaptersRepo(session)
            cycles = CyclesRepo(session)

            # Check for existing active season.
            active = await seasons.get_active()
            if active is not None:
                if not args.force_replace:
                    print(
                        f"ERROR: Ya existe una temporada activa '{active.slug}'. "
                        "Usá --force-replace para reemplazarla.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                await seasons.mark_inactive(active.id)
                print(f"Temporada anterior '{active.slug}' marcada como inactiva.")

            season_id = await seasons.insert(
                slug=data["slug"],
                title=data["title"],
                bible_json=bible_json,
                started_on=started_on,
            )

            chapter_id = await chapters.insert(
                season_id=season_id,
                day_index=1,
                title=data["chapter"]["title"],
                synopsis=data["chapter"]["synopsis"],
                manifest_json=chapter_manifest,
                status="ready",
            )

            cycle_id = await cycles.insert(
                season_id=season_id,
                chapter_id=chapter_id,
                cycle_date=cycle_date,
            )

            await session.commit()
    finally:
        await engine.dispose()

    return BootstrapResult(
        season_id=season_id,
        chapter_id=chapter_id,
        cycle_id=cycle_id,
        slug=data["slug"],
        cycle_date=cycle_date,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _print_result(result: BootstrapResult) -> None:
    print(
        f"\nBootstrap completado para temporada '{result.slug}':\n"
        f"  season_id  : {result.season_id}\n"
        f"  chapter_id : {result.chapter_id}\n"
        f"  cycle_id   : {result.cycle_id}\n"
        f"  cycle_date : {result.cycle_date}\n"
        f"\nEl ciclo está en PENDING_RELEASE. "
        "El tick de las 12:00 ART lo moverá a ESTRENO."
    )


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(_run(args))
    _print_result(result)


if __name__ == "__main__":
    main()
