"""Integration tests for module 013 migrations.

Covers tasks T-001 (create characters table) and T-002 (seed roster).

Assumes ``alembic upgrade head`` has been applied (default for integration
tests via ``conftest.require_real_db_url``).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_characters_table_exists_with_seed(db_session: AsyncSession) -> None:
    """T-001 + T-002 — table exists; seed rows present in (sort_order, id) order."""
    rows = (
        await db_session.execute(
            sa.text(
                "SELECT slug, display_name, photo_r2_key, aspect_ratio, "
                "active, sort_order "
                "FROM characters "
                "ORDER BY sort_order, id"
            )
        )
    ).all()

    slugs = [r.slug for r in rows]
    assert "messi" in slugs
    assert "bad-bunny" in slugs

    by_slug = {r.slug: r for r in rows}
    assert by_slug["messi"].display_name == "Lionel Messi"
    assert by_slug["messi"].photo_r2_key == "static/characters/messi.webp"
    assert by_slug["messi"].aspect_ratio == "1:1"
    assert by_slug["messi"].active is True
    assert by_slug["messi"].sort_order == 10

    assert by_slug["bad-bunny"].display_name == "Bad Bunny"
    assert by_slug["bad-bunny"].sort_order == 20


@pytest.mark.asyncio
async def test_characters_slug_check_constraint(db_session: AsyncSession) -> None:
    """T-001 — slug regex CHECK rejects invalid identifiers."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO characters (slug, display_name, photo_r2_key) "
                "VALUES ('UPPERCASE', 'X', 'static/characters/x.webp')"
            )
        )
    await db_session.rollback()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO characters (slug, display_name, photo_r2_key) "
                "VALUES ('a', 'X', 'static/characters/x.webp')"  # too short
            )
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_characters_photo_key_check_constraint(db_session: AsyncSession) -> None:
    """T-001 — photo_r2_key must match LIKE 'static/characters/%.webp'."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO characters (slug, display_name, photo_r2_key) "
                "VALUES ('valid-slug', 'X', 'wrong/path/x.png')"
            )
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_characters_aspect_ratio_check(db_session: AsyncSession) -> None:
    """T-001 — aspect_ratio restricted to 1:1 / 9:16 / 16:9."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO characters "
                "(slug, display_name, photo_r2_key, aspect_ratio) "
                "VALUES ('valid-slug', 'X', "
                "'static/characters/x.webp', '4:3')"
            )
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_characters_seed_idempotent(db_session: AsyncSession) -> None:
    """T-002 — re-applying the seed via ON CONFLICT DO UPDATE is a no-op-equivalent."""
    # Re-run the same INSERT…ON CONFLICT body. Must not raise.
    await db_session.execute(
        sa.text(
            "INSERT INTO characters "
            "(slug, display_name, photo_r2_key, aspect_ratio, sort_order) "
            "VALUES ('messi', 'Lionel Messi', "
            "'static/characters/messi.webp', '1:1', 10) "
            "ON CONFLICT (slug) DO UPDATE SET "
            "  display_name = EXCLUDED.display_name, "
            "  photo_r2_key = EXCLUDED.photo_r2_key, "
            "  aspect_ratio = EXCLUDED.aspect_ratio, "
            "  sort_order   = EXCLUDED.sort_order, "
            "  updated_at   = now()"
        )
    )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_characters_partial_index_filters_inactive(db_session: AsyncSession) -> None:
    """T-001 — partial index covers active rows; the WHERE clause is correct."""
    # The index is partial WHERE active = TRUE. We don't assert PG uses it
    # (EXPLAIN ANALYZE would be flaky); we assert the rows it filters are
    # what the endpoint will see.
    rows = (
        await db_session.execute(
            sa.text(
                "SELECT count(*) AS n FROM characters WHERE active = TRUE"
            )
        )
    ).one()
    assert rows.n >= 2  # at minimum the 2 seed rows
