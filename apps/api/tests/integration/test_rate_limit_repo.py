"""Integration tests: RateLimitRepo.

Module 002 / Task T-010.

Uses ``db_session`` (rollback on teardown — each test starts with a clean
bucket slate).

All tests skip when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.rate_limit_repo import RateLimited, RateLimitRepo

# ---------------------------------------------------------------------------
# check_and_increment()
# ---------------------------------------------------------------------------


async def test_first_increment_returns_one(db_session: AsyncSession) -> None:
    repo = RateLimitRepo(db_session)
    count = await repo.check_and_increment("test:bucket:001", max_per_window=10)
    assert count == 1


async def test_increments_accumulate(db_session: AsyncSession) -> None:
    repo = RateLimitRepo(db_session)
    key = "test:bucket:002"
    for expected in range(1, 6):
        count = await repo.check_and_increment(key, max_per_window=10)
        assert count == expected


async def test_different_keys_are_independent(db_session: AsyncSession) -> None:
    repo = RateLimitRepo(db_session)
    await repo.check_and_increment("test:key:A", max_per_window=10)
    await repo.check_and_increment("test:key:A", max_per_window=10)

    # key B starts fresh at 1
    count_b = await repo.check_and_increment("test:key:B", max_per_window=10)
    assert count_b == 1


async def test_raises_rate_limited_when_exceeded(db_session: AsyncSession) -> None:
    repo = RateLimitRepo(db_session)
    key = "test:bucket:limit"
    max_count = 3

    # Consume up to the limit
    for _ in range(max_count):
        await repo.check_and_increment(key, max_per_window=max_count)

    # Next call should raise
    with pytest.raises(RateLimited) as exc_info:
        await repo.check_and_increment(key, max_per_window=max_count)

    err = exc_info.value
    assert err.bucket_key == key
    assert err.count == max_count + 1
    assert err.max_count == max_count


async def test_rate_limited_exception_message(db_session: AsyncSession) -> None:
    repo = RateLimitRepo(db_session)
    key = "test:bucket:msg"
    await repo.check_and_increment(key, max_per_window=1)

    with pytest.raises(RateLimited, match="Rate limit excedido"):
        await repo.check_and_increment(key, max_per_window=1)


async def test_max_of_one_allows_exactly_one(db_session: AsyncSession) -> None:
    repo = RateLimitRepo(db_session)
    key = "test:bucket:one"

    count = await repo.check_and_increment(key, max_per_window=1)
    assert count == 1

    with pytest.raises(RateLimited):
        await repo.check_and_increment(key, max_per_window=1)
