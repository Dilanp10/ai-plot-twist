"""Unit tests: GET /api/v1/internal/health/cycle.

Module 003 / Task T-018.

DB session replaced with AsyncMock; repos patched at module level so no
real DB connection is made.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.infra.cycles_repo import CycleRow
from app.infra.system_flags_repo import FlagValue
from app.infra.transitions_repo import TransitionRow
from app.main import create_app
from app.settings import get_settings

_URL = "/api/v1/internal/health/cycle"

_NOW = datetime(2026, 6, 10, 15, 0, 0, tzinfo=UTC)  # 12:00 ART


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mock_session() -> AsyncSession:  # type: ignore[misc]
    yield AsyncMock(spec=AsyncSession)


def _make_cycle(state: str = "RECEPCION_IDEAS") -> CycleRow:
    from datetime import date

    return CycleRow(
        id=1,
        season_id=2,
        chapter_id=5,
        next_chapter_id=None,
        state=state,
        state_entered_at=datetime(2026, 6, 10, 15, 0, 0, tzinfo=UTC),
        cycle_date=date(2026, 6, 10),
    )


def _make_transition(idx: int = 0) -> TransitionRow:
    return TransitionRow(
        id=10 + idx,
        cycle_id=1,
        from_state="ESTRENO",
        to_state="RECEPCION_IDEAS",
        triggered_by="cron",
        trigger_id=f"run-{idx}",
        payload_json=None,
        created_at=datetime(2026, 6, 10, 15, 0, 0, tzinfo=UTC),
    )


def _make_flag(on: bool = False, reason: str | None = None) -> FlagValue:
    return FlagValue(
        flag_key="kill_switch",
        flag_value={"on": on, "reason": reason},
        updated_by="admin",
        updated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", "test-tick-secret")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    get_settings.cache_clear()

    a = create_app()
    a.dependency_overrides[get_session] = _mock_session
    try:
        yield a
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers to reduce patch boilerplate
# ---------------------------------------------------------------------------


def _patch_repos(
    *,
    cycle: CycleRow | None = None,
    transitions: list[TransitionRow] | None = None,
    flag: FlagValue | None = None,
) -> ExitStack:
    """Context manager stack that patches all three repos."""
    stack = ExitStack()

    mock_cycles = stack.enter_context(
        patch("app.api.internal_health_cycle.CyclesRepo")
    )
    mock_cycles.return_value.get_active = AsyncMock(return_value=cycle)

    mock_transitions = stack.enter_context(
        patch("app.api.internal_health_cycle.TransitionsRepo")
    )
    mock_transitions.return_value.get_recent = AsyncMock(
        return_value=transitions or []
    )

    mock_flags = stack.enter_context(
        patch("app.api.internal_health_cycle.SystemFlagsRepo")
    )
    mock_flags.return_value.get = AsyncMock(return_value=flag)

    return stack


# ---------------------------------------------------------------------------
# No active cycle
# ---------------------------------------------------------------------------


def test_no_active_cycle_returns_200_null_state(client: TestClient) -> None:
    with _patch_repos(cycle=None, flag=_make_flag(on=False)):
        r = client.get(_URL)

    assert r.status_code == 200
    data = r.json()
    assert data["cycle_id"] is None
    assert data["current_state"] is None
    assert data["elapsed_seconds"] is None
    assert data["last_transitions"] == []
    # next_ticks is always populated
    assert len(data["next_ticks"]) == 4


# ---------------------------------------------------------------------------
# Active cycle — state + elapsed
# ---------------------------------------------------------------------------


def test_active_cycle_returns_state(client: TestClient) -> None:
    with _patch_repos(cycle=_make_cycle("RECEPCION_IDEAS"), flag=_make_flag()):
        r = client.get(_URL)

    assert r.status_code == 200
    data = r.json()
    assert data["cycle_id"] == 1
    assert data["chapter_id"] == 5
    assert data["season_id"] == 2
    assert data["current_state"] == "RECEPCION_IDEAS"
    assert data["state_entered_at"] is not None
    assert isinstance(data["elapsed_seconds"], float)


# ---------------------------------------------------------------------------
# Transitions serialized correctly
# ---------------------------------------------------------------------------


def test_last_transitions_shape(client: TestClient) -> None:
    transitions = [_make_transition(i) for i in range(3)]
    with _patch_repos(cycle=_make_cycle(), transitions=transitions):
        r = client.get(_URL)

    data = r.json()
    assert len(data["last_transitions"]) == 3
    t = data["last_transitions"][0]
    assert "id" in t
    assert "from_state" in t
    assert "to_state" in t
    assert "triggered_by" in t
    assert "trigger_id" in t
    assert "created_at" in t


# ---------------------------------------------------------------------------
# Kill-switch reflected
# ---------------------------------------------------------------------------


def test_kill_switch_on_reflected(client: TestClient) -> None:
    with _patch_repos(
        cycle=_make_cycle(),
        flag=_make_flag(on=True, reason="rebuild bible"),
    ):
        r = client.get(_URL)

    data = r.json()
    assert data["kill_switch"]["on"] is True
    assert data["kill_switch"]["reason"] == "rebuild bible"


def test_kill_switch_off_when_flag_none(client: TestClient) -> None:
    """SystemFlagsRepo.get returns None → kill_switch defaults to off."""
    with _patch_repos(cycle=_make_cycle(), flag=None):
        r = client.get(_URL)

    data = r.json()
    assert data["kill_switch"]["on"] is False
    assert data["kill_switch"]["reason"] is None


# ---------------------------------------------------------------------------
# Next ticks
# ---------------------------------------------------------------------------


def test_next_ticks_has_four_entries(client: TestClient) -> None:
    with _patch_repos(cycle=None):
        r = client.get(_URL)

    ticks = r.json()["next_ticks"]
    assert len(ticks) == 4
    for tick in ticks:
        assert "tick" in tick
        assert "fires_at_utc" in tick
        assert tick["tick"] in {"ESTRENO", "FILTERING", "GENERACION", "WATCHDOG"}


def test_next_ticks_are_in_chronological_order(client: TestClient) -> None:
    with _patch_repos(cycle=None):
        r = client.get(_URL)

    ticks = r.json()["next_ticks"]
    times = [t["fires_at_utc"] for t in ticks]
    assert times == sorted(times)


# ---------------------------------------------------------------------------
# Response is always 200
# ---------------------------------------------------------------------------


def test_always_returns_200(client: TestClient) -> None:
    with _patch_repos(cycle=None, flag=None):
        r = client.get(_URL)
    assert r.status_code == 200
