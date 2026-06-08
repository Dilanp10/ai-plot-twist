"""Shared utilities for pytest fixture modules (module 002 / T-003)."""

from __future__ import annotations

import os

import pytest


def require_real_db_url() -> str:
    """Return DATABASE_URL or skip the current test if it is the placeholder."""
    from tests.conftest import _is_placeholder_database_url

    url = os.environ.get("DATABASE_URL", "")
    if not url or _is_placeholder_database_url(url):
        pytest.skip(
            "DATABASE_URL no apunta a una base real. "
            "Levantá Postgres (`pnpm db:up`) para correr este test."
        )
    return url
