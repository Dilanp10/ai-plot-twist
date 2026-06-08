"""Pytest bootstrap for the API test suite.

This file runs **before** any test module is imported. It seeds safe default
values for required env vars so that importing ``app.main`` (which uvicorn
expects to expose ``app`` at module scope) does not fail during pytest's
collection phase on a developer machine without ``.env.local`` configured.

Tests that need to assert behavior on **absent** env vars (e.g.
``test_missing_required_raises``) MUST ``monkeypatch.delenv`` them first.

Tests that need a **real** Postgres connection (``test_db.py``,
``test_migrations.py``) detect the placeholder URL below and ``pytest.skip``
when no real DB is reachable. To run those tests against a live DB, export
``DATABASE_URL`` *before* invoking pytest with a real connection string —
``os.environ.setdefault`` will preserve it.
"""

from __future__ import annotations

import os

# Sentinel that ``test_db`` / ``test_migrations`` look for to decide whether
# to skip. Importable from this module so tests stay in sync.
PLACEHOLDER_DATABASE_URL = "postgresql+asyncpg://__placeholder__@localhost:1/__none__"


def _is_placeholder_database_url(url: str) -> bool:
    """Return True iff *url* is the conftest's placeholder (not a real DB)."""
    return "__placeholder__" in url


# Seed defaults — only if the var is not already set in the environment.
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("TICK_SECRET", "test-tick-secret")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("DATABASE_URL", PLACEHOLDER_DATABASE_URL)
