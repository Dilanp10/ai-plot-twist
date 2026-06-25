"""``GET /api/v1/characters`` — public roster used by the twist proposal UI.

Module 013 / Task T-005.

JWT-protected read-only endpoint. Returns the active characters of the
catalog with their public photo URL. Supports ``If-None-Match`` so the
PWA can short-circuit the round trip when the catalog has not changed.

HTTP status semantics:
  * 200 — list returned (possibly empty), ``ETag`` + ``Cache-Control``.
  * 304 — caller's ``If-None-Match`` matches; empty body.
  * 401 — missing or invalid JWT.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.infra.characters_repo import CharacterRow, CharactersRepo
from app.infra.users_repo import UserRow
from app.logging import get_logger
from app.middleware.jwt_auth import require_user
from app.settings import Settings, get_settings

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/characters", tags=["characters"])


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    """Immutable DTO base."""

    model_config = ConfigDict(frozen=True)


class CharacterDTO(_Frozen):
    """A single catalog entry as exposed to clients."""

    id: int = Field(..., ge=1)
    slug: str = Field(..., min_length=2, max_length=40)
    display_name: str = Field(..., min_length=2, max_length=60)
    photo_url: str = Field(..., min_length=1)
    aspect_ratio: str = Field(..., pattern="^(1:1|9:16|16:9)$")


class CharactersListDTO(_Frozen):
    """List wrapper — leaves room for future pagination keys."""

    characters: list[CharacterDTO]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_photo_url(photo_r2_key: str, public_base: str) -> str:
    """Join an R2 key with the public base URL, trailing-slash safe.

    ``photo_r2_key`` is stored as a relative path (``static/characters/messi.webp``).
    ``public_base`` may or may not have a trailing slash; the function
    normalises to a single slash regardless.
    """
    base = public_base.rstrip("/")
    key = photo_r2_key.lstrip("/")
    return f"{base}/{key}"


def _compute_etag(rows: list[CharacterRow], public_base: str) -> str:
    """Deterministic ETag over the ordered, public-facing payload.

    Hash inputs intentionally exclude ``active`` / ``sort_order`` — they
    are not user-visible, so a sort-only change *should* invalidate the
    ETag (it changes the visible order) but a hidden row going inactive
    *should* too (it disappears). Hashing the ordered visible tuple
    captures both.
    """
    material = json.dumps(
        [
            {
                "id": r.id,
                "slug": r.slug,
                "display_name": r.display_name,
                "photo_url": build_photo_url(r.photo_r2_key, public_base),
                "aspect_ratio": r.aspect_ratio,
            }
            for r in rows
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f'"{digest}"'


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listCharacters",
    summary="Active characters catalog (I2V seed roster)",
    response_model=CharactersListDTO,
)
async def list_characters(
    request: Request,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    user: UserRow = Depends(require_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """List active characters with ETag + 5 min private cache."""
    repo = CharactersRepo(db)
    rows = await repo.list_active()

    public_base = settings.r2_public_base_url or ""
    etag = _compute_etag(rows, public_base)

    cache_headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=300",
    }

    if if_none_match is not None and if_none_match == etag:
        _log.info(
            "characters_fetched",
            count=len(rows),
            etag=etag,
            if_none_match_hit=True,
            user_id=user.id,
        )
        return Response(status_code=304, headers=cache_headers)

    payload = CharactersListDTO(
        characters=[
            CharacterDTO(
                id=r.id,
                slug=r.slug,
                display_name=r.display_name,
                photo_url=build_photo_url(r.photo_r2_key, public_base),
                aspect_ratio=r.aspect_ratio,
            )
            for r in rows
        ]
    )
    _log.info(
        "characters_fetched",
        count=len(rows),
        etag=etag,
        if_none_match_hit=False,
        user_id=user.id,
    )
    return JSONResponse(
        status_code=200,
        content=payload.model_dump(),
        headers=cache_headers,
    )
