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
from app.scripts.upload_static_assets import _ASSETS, _ASSETS_DIR, _upload_one


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
