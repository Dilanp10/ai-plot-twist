"""CLI: upload-static-assets — push placeholder binaries to Cloudflare R2.

Module 008 / Task T-017 delta.

Usage (direct):
    uv run python -m app.scripts.upload_static_assets

Reads R2 credentials and ``R2_PUBLIC_BASE_URL`` from environment / .env.local.
Uploads each file under ``assets/`` listed below to ``static/<basename>``
and prints the resulting public URLs so the operator can copy them into
``GENERATION_PLACEHOLDER_URL`` / ``GENERATION_PLACEHOLDER_VIDEO_URL``.

Exits 0 on success; non-zero on any upload failure or missing credentials.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from app.infra.r2_uploader import R2Uploader, R2UploadError
from app.settings import get_settings

_log = logging.getLogger(__name__)

# Repo-root-relative path to the static-asset directory.
# This script lives at apps/api/app/scripts/upload_static_assets.py,
# so four .parent hops reach the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_ASSETS_DIR = _REPO_ROOT / "assets"


@dataclass(frozen=True)
class _AssetSpec:
    filename: str
    content_type: str


# Order matters only for log readability.
_ASSETS: tuple[_AssetSpec, ...] = (
    _AssetSpec(filename="placeholder.mp4", content_type="video/mp4"),
    _AssetSpec(filename="placeholder.webp", content_type="image/webp"),
)


async def _upload_one(uploader: R2Uploader, spec: _AssetSpec) -> str | None:
    """Upload one asset; return its public URL or ``None`` if the file is missing."""
    src = _ASSETS_DIR / spec.filename
    if not src.exists():
        _log.warning("asset_missing path=%s skip=True", src)
        return None
    body = src.read_bytes()
    key = f"static/{spec.filename}"
    url = await uploader.upload(key, body, spec.content_type)
    _log.info(
        "asset_uploaded filename=%s bytes=%d key=%s url=%s",
        spec.filename,
        len(body),
        key,
        url,
    )
    return url


async def _main_async() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", settings.r2_account_id),
            ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
            ("R2_BUCKET", settings.r2_bucket),
            ("R2_PUBLIC_BASE_URL", settings.r2_public_base_url),
        )
        if not value
    ]
    if missing:
        _log.error("r2_credentials_missing vars=%s", ",".join(missing))
        return 1

    # mypy: settings.r2_* are str|None; the missing check above narrows them.
    assert settings.r2_account_id is not None
    assert settings.r2_access_key_id is not None
    assert settings.r2_secret_access_key is not None
    assert settings.r2_bucket is not None
    assert settings.r2_public_base_url is not None

    uploader = R2Uploader(
        account_id=settings.r2_account_id,
        key_id=settings.r2_access_key_id,
        secret=settings.r2_secret_access_key,
        bucket=settings.r2_bucket,
        public_base_url=settings.r2_public_base_url,
    )

    any_uploaded = False
    for spec in _ASSETS:
        try:
            url = await _upload_one(uploader, spec)
        except R2UploadError as exc:
            _log.error("asset_upload_failed filename=%s error=%s", spec.filename, exc)
            return 2
        if url is not None:
            any_uploaded = True

    if not any_uploaded:
        _log.error("no_assets_found dir=%s", _ASSETS_DIR)
        return 3
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main_async()))


if __name__ == "__main__":  # pragma: no cover
    main()
