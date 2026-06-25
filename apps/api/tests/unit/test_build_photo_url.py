"""Unit tests for the ``build_photo_url`` helper.

Module 013 / Task T-005.

The helper joins a relative R2 key with a public base URL, normalising
trailing/leading slashes. Cheap to test, no DB required.
"""

from __future__ import annotations

import pytest

from app.api.characters import build_photo_url


@pytest.mark.parametrize(
    ("base", "key", "expected"),
    [
        # Happy: base without trailing slash + key without leading slash.
        (
            "https://r2-public.example",
            "static/characters/messi.webp",
            "https://r2-public.example/static/characters/messi.webp",
        ),
        # Trailing slash on base — must collapse.
        (
            "https://r2-public.example/",
            "static/characters/messi.webp",
            "https://r2-public.example/static/characters/messi.webp",
        ),
        # Leading slash on key — must collapse.
        (
            "https://r2-public.example",
            "/static/characters/messi.webp",
            "https://r2-public.example/static/characters/messi.webp",
        ),
        # Both leading + trailing — must still collapse to single slash.
        (
            "https://r2-public.example/",
            "/static/characters/messi.webp",
            "https://r2-public.example/static/characters/messi.webp",
        ),
        # Multiple trailing/leading slashes.
        (
            "https://r2-public.example///",
            "///static/characters/messi.webp",
            "https://r2-public.example/static/characters/messi.webp",
        ),
        # Empty base — degenerate but defined: yields a root-relative URL.
        ("", "static/characters/messi.webp", "/static/characters/messi.webp"),
    ],
)
def test_build_photo_url(base: str, key: str, expected: str) -> None:
    assert build_photo_url(key, base) == expected
