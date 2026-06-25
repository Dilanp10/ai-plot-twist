"""Integration tests for ``CharactersRepo`` (module 013 / Task T-003).

Assumes the seed migration (T-002) has been applied. Both Messi and
Bad Bunny are expected to be present and active.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.characters_repo import CharactersRepo


@pytest.mark.asyncio
async def test_list_active_returns_seeded_rows_in_order(
    db_session: AsyncSession,
) -> None:
    """T-003 — ``list_active`` returns ≥2 rows ordered (sort_order ASC, id ASC)."""
    repo = CharactersRepo(db_session)
    rows = await repo.list_active()

    slugs = [r.slug for r in rows]
    assert "messi" in slugs
    assert "bad-bunny" in slugs
    # Messi has sort_order=10, Bad Bunny=20 → Messi must come first.
    messi_idx = slugs.index("messi")
    bunny_idx = slugs.index("bad-bunny")
    assert messi_idx < bunny_idx

    messi = next(r for r in rows if r.slug == "messi")
    assert messi.display_name == "Lionel Messi"
    assert messi.photo_r2_key == "static/characters/messi.webp"
    assert messi.aspect_ratio == "1:1"


@pytest.mark.asyncio
async def test_get_by_id_if_active_returns_row(db_session: AsyncSession) -> None:
    """T-003 — ``get_by_id_if_active`` returns the row for an active id."""
    repo = CharactersRepo(db_session)
    rows = await repo.list_active()
    messi = next(r for r in rows if r.slug == "messi")

    fetched = await repo.get_by_id_if_active(messi.id)
    assert fetched is not None
    assert fetched.slug == "messi"
    assert fetched.display_name == "Lionel Messi"


@pytest.mark.asyncio
async def test_get_by_id_if_active_returns_none_for_missing(
    db_session: AsyncSession,
) -> None:
    """T-003 — ``get_by_id_if_active`` returns None for a missing id."""
    repo = CharactersRepo(db_session)
    assert await repo.get_by_id_if_active(999_999_999) is None
