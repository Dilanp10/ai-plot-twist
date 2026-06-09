"""Integration tests: issue_invite CLI.

Module 002 / Task T-011.

Tests call ``_run()`` directly (avoids subprocess overhead) and verify
side-effects via the ``db_session`` fixture. Skip without a real DB.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.scripts.issue_invite import _run, build_parser
from tests.fixtures import require_real_db_url

# ---------------------------------------------------------------------------
# Parser (no DB needed)
# ---------------------------------------------------------------------------


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.count == 1
    assert args.ttl_days == 30
    assert args.note is None
    assert args.allow_prod is False


def test_parser_all_flags() -> None:
    args = build_parser().parse_args(
        ["--count", "5", "--ttl-days", "7", "--note", "para lucía", "--allow-prod"]
    )
    assert args.count == 5
    assert args.ttl_days == 7
    assert args.note == "para lucía"
    assert args.allow_prod is True


# ---------------------------------------------------------------------------
# Safety gate (no DB needed — exits before connecting)
# ---------------------------------------------------------------------------


def test_prod_without_allow_prod_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "prod")
    # Clear lru_cache so get_settings() re-reads the patched ENV
    from app.settings import get_settings
    get_settings.cache_clear()

    build_parser().parse_args(["--allow-prod"])  # allow_prod=True → sanity check
    # Restore before test isolation issues
    get_settings.cache_clear()

    # Now test the rejection path (allow_prod=False)
    monkeypatch.setenv("ENV", "prod")
    get_settings.cache_clear()
    args_no_flag = build_parser().parse_args([])
    with pytest.raises(SystemExit):
        import asyncio
        asyncio.run(_run(args_no_flag))

    get_settings.cache_clear()
    monkeypatch.setenv("ENV", "test")
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Happy path (real DB)
# ---------------------------------------------------------------------------


async def test_run_inserts_requested_count(db_session: AsyncSession) -> None:
    """_run() inserts exactly --count rows, all status='unused'."""
    note = f"test-issue-{uuid4().hex[:8]}"
    args = argparse.Namespace(
        count=3,
        ttl_days=1,
        note=note,
        display_name_hint=None,
        allow_prod=False,
    )

    rows = await _run(args)

    assert len(rows) == 3
    assert all(r.status == "unused" for r in rows)
    assert all(r.note == note for r in rows)
    # expires_at should be ~1 day from now
    for r in rows:
        delta = (r.expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
        assert 0 < delta < 86400 + 10  # within 1 day + tolerance

    # Cleanup rows committed by _run (db_session rollback won't reach them)
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as s:
        await s.execute(sa.text("DELETE FROM invites WHERE note = :n"), {"n": note})
        await s.commit()
    await engine.dispose()


async def test_run_sets_issued_by_with_hint(db_session: AsyncSession) -> None:
    note = f"test-hint-{uuid4().hex[:8]}"
    args = argparse.Namespace(
        count=1,
        ttl_days=1,
        note=note,
        display_name_hint="lucía",
        allow_prod=False,
    )

    rows = await _run(args)
    assert "lucía" in rows[0].issued_by

    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as s:
        await s.execute(sa.text("DELETE FROM invites WHERE note = :n"), {"n": note})
        await s.commit()
    await engine.dispose()
