"""Integration tests: bootstrap_cycle script.

Module 003 / Task T-019.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).

Covers:
  - Happy path: YAML → season + chapter + cycle rows in DB.
  - Slug mismatch: --season flag does not match YAML slug → sys.exit(1).
  - Conflict without --force-replace: existing active season → sys.exit(1).
  - --force-replace: existing season deactivated, new one inserted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import textwrap
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.chapters_repo import ChaptersRepo
from app.infra.cycles_repo import CyclesRepo
from app.infra.seasons_repo import SeasonsRepo
from app.scripts.bootstrap_cycle import BootstrapResult, _run

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"
_SLUG_PREFIX = "_bs-test-"


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> str:
    from tests.conftest import _is_placeholder_database_url

    url = os.environ.get("DATABASE_URL", "")
    if not url or _is_placeholder_database_url(url):
        pytest.skip("DATABASE_URL no apunta a una base real.")
    return url


@pytest.fixture(scope="module", autouse=True)
def _ensure_migrated(database_url: str) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            asyncio.to_thread(
                command.upgrade, _alembic_config(database_url), "head"
            )
        )
    finally:
        loop.close()


@pytest.fixture
async def session(database_url: str) -> AsyncSession:  # type: ignore[misc]
    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _cleanup(database_url: str) -> None:  # type: ignore[misc]
    """Delete test rows after each test (seasons CASCADE to chapters + cycles)."""
    yield
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'")
        )
    await engine.dispose()


def _make_yaml(tmp_path: Path, slug: str) -> Path:
    """Write a minimal YAML manifest to a temp file and return the path."""
    content = textwrap.dedent(f"""\
        slug: {slug}
        title: "Test Season"
        started_on: "2026-06-10"
        bible:
          logline: "Test logline"
        chapter:
          title: "Capítulo Test"
          synopsis: "Test synopsis"
          manifest:
            panels: []
    """)
    p = tmp_path / f"{slug}.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _args(slug: str, yaml_path: Path, *, force_replace: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        season=slug,
        day_zero_manifest=str(yaml_path),
        force_replace=force_replace,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_creates_all_rows(
    tmp_path: Path,
    database_url: str,
    session: AsyncSession,
) -> None:
    slug = f"{_SLUG_PREFIX}happy"
    yaml_path = _make_yaml(tmp_path, slug)
    args = _args(slug, yaml_path)

    result: BootstrapResult = await _run(args, database_url=database_url)

    assert isinstance(result, BootstrapResult)
    assert result.slug == slug
    assert result.season_id > 0
    assert result.chapter_id > 0
    assert result.cycle_id > 0

    # Verify DB rows exist.
    season = await SeasonsRepo(session).get_by_slug(slug)
    assert season is not None
    assert season.is_active is True

    chapter = await ChaptersRepo(session).get_by_id(result.chapter_id)
    assert chapter is not None
    assert chapter.day_index == 1
    assert chapter.status == "ready"

    cycle = await CyclesRepo(session).get_active()
    assert cycle is not None
    assert cycle.state == "PENDING_RELEASE"
    assert cycle.id == result.cycle_id


# ---------------------------------------------------------------------------
# Slug mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slug_mismatch_exits_1(
    tmp_path: Path,
    database_url: str,
) -> None:
    yaml_slug = f"{_SLUG_PREFIX}yaml-slug"
    cli_slug = f"{_SLUG_PREFIX}cli-slug"
    yaml_path = _make_yaml(tmp_path, yaml_slug)
    args = _args(cli_slug, yaml_path)  # mismatch

    with pytest.raises(SystemExit) as exc_info:
        await _run(args, database_url=database_url)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Active season conflict (without --force-replace)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_active_season_exits_1_without_force(
    tmp_path: Path,
    database_url: str,
) -> None:
    slug = f"{_SLUG_PREFIX}conflict"
    yaml_path = _make_yaml(tmp_path, slug)

    # First bootstrap succeeds.
    await _run(_args(slug, yaml_path), database_url=database_url)

    # Second bootstrap (same slug, no --force-replace) should fail.
    slug2 = f"{_SLUG_PREFIX}conflict2"
    yaml_path2 = _make_yaml(tmp_path, slug2)
    with pytest.raises(SystemExit) as exc_info:
        await _run(_args(slug2, yaml_path2), database_url=database_url)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# --force-replace deactivates old season
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_replace_deactivates_old_season(
    tmp_path: Path,
    database_url: str,
    session: AsyncSession,
) -> None:
    slug1 = f"{_SLUG_PREFIX}old"
    slug2 = f"{_SLUG_PREFIX}new"
    yaml1 = _make_yaml(tmp_path, slug1)
    yaml2 = _make_yaml(tmp_path, slug2)

    # Bootstrap season 1.
    await _run(_args(slug1, yaml1), database_url=database_url)

    # Bootstrap season 2 with --force-replace.
    result2 = await _run(
        _args(slug2, yaml2, force_replace=True),
        database_url=database_url,
    )

    # Season 1 should now be inactive.
    old = await SeasonsRepo(session).get_by_slug(slug1)
    assert old is not None
    assert old.is_active is False

    # Season 2 is active.
    active = await SeasonsRepo(session).get_active()
    assert active is not None
    assert active.slug == slug2
    assert active.id == result2.season_id
