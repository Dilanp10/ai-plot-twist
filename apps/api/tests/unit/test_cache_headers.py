"""Unit tests: cache_headers helpers (module 004 / T-006)."""

from __future__ import annotations

from fastapi import Response

from app.middleware.cache_headers import set_cache, set_etag


def test_max_age_only() -> None:
    r = Response()
    set_cache(r, max_age=60)
    assert r.headers["Cache-Control"] == "public, max-age=60"


def test_max_age_with_swr() -> None:
    r = Response()
    set_cache(r, max_age=60, swr=600)
    assert r.headers["Cache-Control"] == "public, max-age=60, stale-while-revalidate=600"


def test_max_age_with_must_revalidate() -> None:
    r = Response()
    set_cache(r, max_age=60, swr=600, must_revalidate=True)
    assert (
        r.headers["Cache-Control"]
        == "public, max-age=60, stale-while-revalidate=600, must-revalidate"
    )


def test_max_age_with_immutable() -> None:
    r = Response()
    set_cache(r, max_age=86400, immutable=True)
    assert r.headers["Cache-Control"] == "public, max-age=86400, immutable"


def test_swr_zero_is_omitted() -> None:
    r = Response()
    set_cache(r, max_age=60, swr=0)
    assert "stale-while-revalidate" not in r.headers["Cache-Control"]


def test_no_store_overrides_everything() -> None:
    r = Response()
    set_cache(r, max_age=60, swr=600, immutable=True, must_revalidate=True, no_store=True)
    assert r.headers["Cache-Control"] == "no-store"


def test_set_etag_adds_quotes() -> None:
    r = Response()
    set_etag(r, "a1b2c3d4e5f60718")
    assert r.headers["ETag"] == '"a1b2c3d4e5f60718"'


def test_set_etag_does_not_double_quote() -> None:
    """Even if caller passes hex without quotes, helper adds exactly one pair."""
    r = Response()
    set_etag(r, "deadbeefcafe1234")
    assert r.headers["ETag"].startswith('"')
    assert r.headers["ETag"].endswith('"')
    assert r.headers["ETag"].count('"') == 2
