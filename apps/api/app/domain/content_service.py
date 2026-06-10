"""ContentService — orchestrator for the chapter content read endpoints.

Module 004 / Task T-005.

Combines the pure-domain helpers from Phase 0 (``bible_redaction``, ``windows``,
``etag``) with :class:`ContentRepo` (Phase 1) and :class:`SystemFlagsRepo` (from
module 003) into the three call paths used by the HTTP handlers in Phase 2:

  * :meth:`today`   → ``GET /chapters/today``
  * :meth:`chapter` → ``GET /chapters/{public_id}``
  * :meth:`season`  → ``GET /seasons/{slug}``

The service is **transport-agnostic**: it raises typed exceptions and returns
Pydantic DTOs. The HTTP layer maps exceptions to RFC 7807 ``Problem`` responses
per research R-007.

Exception → HTTP mapping (for the handler's reference):

  * :class:`KillSwitchActive`  → 503 ``under_maintenance``  (reuse from 003)
  * :class:`NoActiveSeason`    → 503 ``no_active_season``
  * :class:`NoLiveChapter`     → 404 ``no_live_chapter`` + ``first_release_at``
  * :class:`ChapterNotFound`   → 404 ``chapter_not_found``
  * :class:`SeasonNotFound`    → 404 ``season_not_found``

The kill-switch check happens **before** any data query (spec FR-006). The
``SystemFlagsRepo.is_kill_switch_on()`` call hits a 30 s in-process cache so
the overhead is ~0.5 ms in the worst case (per module 003 R-005).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.bible_redaction import redact
from app.domain.cycle_clock import _ART_TZ
from app.domain.cycle_executor import KillSwitchActive
from app.domain.windows import CycleTimes, Windows, compute_windows
from app.infra.content_repo import ContentRepo, TodayPayload
from app.infra.system_flags_repo import SystemFlagsRepo

# ---------------------------------------------------------------------------
# Re-export so HTTP handlers can `from app.domain.content_service import …`
# ---------------------------------------------------------------------------

__all__ = [
    "ChapterDTO",
    "ChapterNotFound",
    "ChapterResponseDTO",
    "ContentService",
    "KillSwitchActive",
    "NoActiveSeason",
    "NoLiveChapter",
    "PanelDTO",
    "SeasonBriefDTO",
    "SeasonFullDTO",
    "SeasonNotFound",
    "SeasonResponseDTO",
    "TodayResponseDTO",
    "WindowsDTO",
]


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class NoActiveSeason(Exception):
    """No row in ``seasons`` has ``is_active = TRUE``.

    Mapped to 503 ``no_active_season`` by the HTTP layer (per spec edge case).
    Distinct from :class:`KillSwitchActive` so the PWA can render different
    UIs ("intermediate maintenance" vs "no season planned right now").
    """


class NoLiveChapter(Exception):
    """Active cycle exists but no chapter has ``status='live'`` yet.

    Happens on day 0: the cycle is in ``PENDING_RELEASE`` and the bootstrap
    chapter is ``status='ready'`` until the first ESTRENO tick fires.
    """

    def __init__(self, first_release_at: datetime) -> None:
        self.first_release_at = first_release_at
        super().__init__(f"No live chapter yet; first release at {first_release_at.isoformat()}")


class ChapterNotFound(Exception):
    """``public_id`` does not match a chapter with status IN (live, archived)."""

    def __init__(self, public_id: UUID) -> None:
        self.public_id = public_id
        super().__init__(f"Chapter not found: {public_id}")


class SeasonNotFound(Exception):
    """``slug`` does not match any row in ``seasons``."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"Season not found: {slug!r}")


# ---------------------------------------------------------------------------
# DTOs — matching contracts/chapters.yaml
# ---------------------------------------------------------------------------


CycleStateLiteral = Literal[
    "PENDING_RELEASE",
    "ESTRENO",
    "RECEPCION_IDEAS",
    "FILTERING",
    "VOTACION",
    "GENERACION",
    "FAILED",
]


class _Frozen(BaseModel):
    """Common config: immutable DTOs, strict validation."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=False)


class PanelDTO(_Frozen):
    idx: int
    image_url: str
    image_blurhash: str | None = None
    tts_url: str | None = None
    narration: str
    mood: str


class WindowsDTO(_Frozen):
    submit_until: datetime
    vote_from: datetime
    vote_until: datetime
    next_release: datetime

    @classmethod
    def from_windows(cls, w: Windows) -> WindowsDTO:
        return cls(
            submit_until=w.submit_until,
            vote_from=w.vote_from,
            vote_until=w.vote_until,
            next_release=w.next_release,
        )


class SeasonBriefDTO(_Frozen):
    slug: str
    title: str


class SeasonFullDTO(_Frozen):
    slug: str
    title: str
    bible_public: dict[str, Any]
    started_on: Any  # date — Pydantic handles `datetime.date` via Any to keep ConfigDict clean
    ended_on: Any | None = None  # date | None
    chapter_count: int
    current_day_index: int | None


class ChapterDTO(_Frozen):
    id: UUID
    day_index: int
    title: str
    synopsis: str
    released_at: datetime
    panels: list[PanelDTO]
    cliffhanger: str


class TodayResponseDTO(_Frozen):
    cycle_state: CycleStateLiteral
    season: SeasonBriefDTO
    chapter: ChapterDTO
    windows: WindowsDTO


class ChapterResponseDTO(_Frozen):
    season: SeasonBriefDTO
    chapter: ChapterDTO


class SeasonResponseDTO(_Frozen):
    season: SeasonFullDTO


# ---------------------------------------------------------------------------
# Manifest parsing (tolerant — per spec edge case "broken R2 URLs surface as-is")
# ---------------------------------------------------------------------------


def _panels_from_manifest(manifest: dict[str, Any]) -> list[PanelDTO]:
    """Build a panel list from ``chapter.manifest_json["panels"]``.

    Tolerant: missing keys, wrong types, and empty inputs all return ``[]``
    or default fields rather than raising. The PWA renders a panel-level
    placeholder when ``image_url`` is empty (spec edge case).
    """
    raw = manifest.get("panels")
    if not isinstance(raw, list):
        return []
    out: list[PanelDTO] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            PanelDTO(
                idx=int(item.get("idx", 0)),
                image_url=str(item.get("image_url", "")),
                image_blurhash=_opt_str(item.get("image_blurhash")),
                tts_url=_opt_str(item.get("tts_url")),
                narration=str(item.get("narration", "")),
                mood=str(item.get("mood", "")),
            )
        )
    return out


def _cliffhanger_from_manifest(manifest: dict[str, Any]) -> str:
    raw = manifest.get("cliffhanger")
    return str(raw) if raw is not None else ""


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


# ---------------------------------------------------------------------------
# First-release-at derivation (used by NoLiveChapter)
# ---------------------------------------------------------------------------


def _first_release_at_utc(payload: TodayPayload, cycle_times: CycleTimes) -> datetime:
    """When the bootstrap chapter will go live: cycle_date @ ESTRENO ART → UTC.

    Equivalent to ``compute_windows(...).next_release - 1 day`` but avoids
    the indirection. Returns a tz-aware UTC datetime.
    """
    art_local = datetime.combine(payload.cycle_date, cycle_times.estreno_art, tzinfo=_ART_TZ)
    return art_local.astimezone(UTC)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ContentService:
    """Orchestrate the three chapter content reads.

    Parameters
    ----------
    content_repo:
        Read-only repo over ``cycles``/``chapters``/``seasons`` (T-004).
    flags_repo:
        Source of truth for ``kill_switch`` (module 003 T-011).
    cycle_times:
        ART times-of-day for ESTRENO / FILTERING / GENERACION (T-002).
    now_utc:
        Clock source. Defaults to ``datetime.now(UTC)``. Injectable for
        deterministic tests.
    """

    def __init__(
        self,
        content_repo: ContentRepo,
        flags_repo: SystemFlagsRepo,
        cycle_times: CycleTimes,
        now_utc: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repo = content_repo
        self._flags = flags_repo
        self._cycle_times = cycle_times
        self._now = now_utc

    # ── today ───────────────────────────────────────────────────────────────

    async def today(self) -> TodayResponseDTO:
        """Resolve ``GET /chapters/today``.

        Raises
        ------
        KillSwitchActive
            ``kill_switch.on = TRUE``.
        NoActiveSeason
            No row with ``seasons.is_active = TRUE``.
        NoLiveChapter
            Active cycle exists but its chapter is not yet ``live``.
        """
        await self._ensure_not_killed()

        payload = await self._repo.get_today_payload()
        if payload is None:
            raise NoActiveSeason

        if payload.chapter_status != "live":
            raise NoLiveChapter(first_release_at=_first_release_at_utc(payload, self._cycle_times))

        # status='live' invariant from module 003 ESTRENO transition: released_at is set.
        assert payload.chapter_released_at is not None, (
            "module 003 invariant violated: status=live with released_at=NULL"
        )

        windows = compute_windows(
            cycle_state=payload.cycle_state,
            state_entered_at=payload.cycle_state_entered_at,
            cycle_date=payload.cycle_date,
            now_utc=self._now(),
            cycle_times=self._cycle_times,
        )

        return TodayResponseDTO(
            cycle_state=_assert_known_state(payload.cycle_state),
            season=SeasonBriefDTO(
                slug=payload.season_slug,
                title=payload.season_title,
            ),
            chapter=ChapterDTO(
                id=payload.chapter_public_id,
                day_index=payload.chapter_day_index,
                title=payload.chapter_title,
                synopsis=payload.chapter_synopsis,
                released_at=payload.chapter_released_at,
                panels=_panels_from_manifest(payload.chapter_manifest_json),
                cliffhanger=_cliffhanger_from_manifest(payload.chapter_manifest_json),
            ),
            windows=WindowsDTO.from_windows(windows),
        )

    # ── chapter(public_id) ──────────────────────────────────────────────────

    async def chapter(self, public_id: UUID) -> ChapterResponseDTO:
        """Resolve ``GET /chapters/{public_id}``.

        Raises
        ------
        KillSwitchActive
        ChapterNotFound
            ``public_id`` is unknown or its chapter is pre-release.
        """
        await self._ensure_not_killed()

        payload = await self._repo.get_chapter_by_public_id(public_id)
        if payload is None:
            raise ChapterNotFound(public_id)

        return ChapterResponseDTO(
            season=SeasonBriefDTO(slug=payload.season_slug, title=payload.season_title),
            chapter=ChapterDTO(
                id=payload.public_id,
                day_index=payload.day_index,
                title=payload.title,
                synopsis=payload.synopsis,
                released_at=payload.released_at,
                panels=_panels_from_manifest(payload.manifest_json),
                cliffhanger=_cliffhanger_from_manifest(payload.manifest_json),
            ),
        )

    # ── season(slug) ────────────────────────────────────────────────────────

    async def season(self, slug: str) -> SeasonResponseDTO:
        """Resolve ``GET /seasons/{slug}``.

        Raises
        ------
        KillSwitchActive
        SeasonNotFound
        """
        await self._ensure_not_killed()

        payload = await self._repo.get_season_by_slug(slug)
        if payload is None:
            raise SeasonNotFound(slug)

        return SeasonResponseDTO(
            season=SeasonFullDTO(
                slug=payload.slug,
                title=payload.title,
                bible_public=redact(payload.bible_json),
                started_on=payload.started_on,
                ended_on=payload.ended_on,
                chapter_count=payload.chapter_count,
                current_day_index=payload.current_day_index,
            )
        )

    # ── Internals ───────────────────────────────────────────────────────────

    async def _ensure_not_killed(self) -> None:
        if await self._flags.is_kill_switch_on():
            flag = await self._flags.get("kill_switch")
            reason: str | None = None
            if flag is not None:
                raw_reason = flag.flag_value.get("reason")
                reason = str(raw_reason) if raw_reason is not None else None
            raise KillSwitchActive(reason)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_KNOWN_STATES: frozenset[str] = frozenset(
    {
        "PENDING_RELEASE",
        "ESTRENO",
        "RECEPCION_IDEAS",
        "FILTERING",
        "VOTACION",
        "GENERACION",
        "FAILED",
    }
)


def _assert_known_state(state: str) -> CycleStateLiteral:
    """Validate that *state* is one of the seven documented FSM states.

    Returns the same string typed as the Literal so Pydantic accepts it
    without a runtime ``ValidationError``. Raises ``ValueError`` if the DB
    ever surfaces an unexpected state — a loud invariant check.
    """
    if state not in _KNOWN_STATES:
        raise ValueError(f"Unknown cycle_state from DB: {state!r}")
    return state  # type: ignore[return-value]
