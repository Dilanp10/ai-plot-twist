"""Unit tests: app.scripts.upload_static_assets.

Module 008 / Task T-017 delta.

Coverage:
  - _ASSETS lists placeholder.mp4 + placeholder.webp.
  - _upload_one returns None when the file is absent.
  - _upload_one calls uploader.upload(key='static/<basename>', ct=...)
    and returns the URL.
  - _main_async exits non-zero when R2 credentials are missing.
  - assets/placeholder.mp4 exists and is non-empty (binary committed).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.infra.r2_uploader import R2Uploader
from app.scripts.upload_static_assets import (
    _ASSETS,
    _ASSETS_DIR,
    _AssetSpec,
    _iter_character_assets,
    _upload_one,
)


def test_assets_list_includes_placeholder_mp4() -> None:
    names = {spec.filename for spec in _ASSETS}
    assert "placeholder.mp4" in names


def test_assets_list_includes_placeholder_webp() -> None:
    names = {spec.filename for spec in _ASSETS}
    assert "placeholder.webp" in names


def test_placeholder_mp4_committed_to_repo() -> None:
    """The placeholder.mp4 binary must be checked in so deploys are reproducible."""
    src = _ASSETS_DIR / "placeholder.mp4"
    assert src.exists(), f"missing committed placeholder: {src}"
    assert src.stat().st_size > 0, "placeholder.mp4 is empty"


@pytest.mark.asyncio
async def test_upload_one_missing_file_returns_none(tmp_path: Path) -> None:
    spec = _ASSETS[0]
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock()
    with patch("app.scripts.upload_static_assets._ASSETS_DIR", new=tmp_path):
        result = await _upload_one(uploader, spec)
    assert result is None
    uploader.upload.assert_not_called()


@pytest.mark.asyncio
async def test_upload_one_uploads_file_and_returns_url(tmp_path: Path) -> None:
    spec = _ASSETS[0]
    body = b"\x00" * 100
    (tmp_path / spec.filename).write_bytes(body)

    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(return_value=f"https://r2.example/static/{spec.filename}")

    with patch("app.scripts.upload_static_assets._ASSETS_DIR", new=tmp_path):
        url = await _upload_one(uploader, spec)

    assert url == f"https://r2.example/static/{spec.filename}"
    args, _ = uploader.upload.call_args
    assert args[0] == f"static/{spec.filename}"
    assert args[1] == body
    assert args[2] == spec.content_type


# ---------------------------------------------------------------------------
# Module 013 / T-007 — character assets walker
# ---------------------------------------------------------------------------


def test_iter_character_assets_absent_dir_yields_nothing(tmp_path: Path) -> None:
    """No ``characters/`` subdir → empty generator (no crash)."""
    assert list(_iter_character_assets(tmp_path)) == []


def test_iter_character_assets_yields_valid_webps(tmp_path: Path) -> None:
    """Each valid ``<slug>.webp`` becomes an ``_AssetSpec``."""
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    (chars_dir / "messi.webp").write_bytes(b"\x00")
    (chars_dir / "bad-bunny.webp").write_bytes(b"\x00")

    specs = list(_iter_character_assets(tmp_path))
    filenames = {s.filename for s in specs}
    assert filenames == {
        "characters/messi.webp",
        "characters/bad-bunny.webp",
    }
    assert all(s.content_type == "image/webp" for s in specs)


def test_iter_character_assets_skips_invalid_names(tmp_path: Path) -> None:
    """Files not matching the slug regex are skipped, run continues."""
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    # Valid:
    (chars_dir / "valid-slug.webp").write_bytes(b"\x00")
    # Invalid — uppercase, space, wrong ext, hidden, too short.
    (chars_dir / "UPPERCASE.webp").write_bytes(b"\x00")
    (chars_dir / "with space.webp").write_bytes(b"\x00")
    (chars_dir / "wrongext.png").write_bytes(b"\x00")
    (chars_dir / ".hidden.webp").write_bytes(b"\x00")
    (chars_dir / "a.webp").write_bytes(b"\x00")  # too short (min 2)

    filenames = {s.filename for s in _iter_character_assets(tmp_path)}
    assert filenames == {"characters/valid-slug.webp"}


def test_iter_character_assets_skips_subdirs(tmp_path: Path) -> None:
    """Sub-directories under ``characters/`` are silently ignored."""
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    (chars_dir / "nested").mkdir()
    (chars_dir / "messi.webp").write_bytes(b"\x00")

    filenames = {s.filename for s in _iter_character_assets(tmp_path)}
    assert filenames == {"characters/messi.webp"}


@pytest.mark.asyncio
async def test_upload_one_uploads_character_to_static_characters_path(
    tmp_path: Path,
) -> None:
    """A spec with the ``characters/`` prefix lands at ``static/characters/<file>``."""
    spec = _AssetSpec(filename="characters/messi.webp", content_type="image/webp")
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    body = b"\x00" * 50
    (chars_dir / "messi.webp").write_bytes(body)

    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(
        return_value="https://r2.example/static/characters/messi.webp"
    )

    with patch("app.scripts.upload_static_assets._ASSETS_DIR", new=tmp_path):
        url = await _upload_one(uploader, spec)

    assert url == "https://r2.example/static/characters/messi.webp"
    args, _ = uploader.upload.call_args
    assert args[0] == "static/characters/messi.webp"
    assert args[1] == body
    assert args[2] == "image/webp"


@pytest.mark.asyncio
async def test_main_async_exits_nonzero_when_credentials_missing() -> None:
    from app.scripts.upload_static_assets import _main_async

    class _StubSettings:
        r2_account_id = None
        r2_access_key_id = "x"
        r2_secret_access_key = "y"
        r2_bucket = "b"
        r2_public_base_url = "https://r2.example"

    with patch("app.scripts.upload_static_assets.get_settings", return_value=_StubSettings()):
        rc = await _main_async()
    assert rc != 0
