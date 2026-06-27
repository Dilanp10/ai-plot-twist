"""Application settings for the AI Plot Twist API.

All configuration is read from environment variables (and optionally a
``.env.local`` file). Required fields raise ``ValidationError`` at process
startup if absent — fail-fast is intentional per spec FR-016.

Usage::

    from app.settings import get_settings

    settings = get_settings()          # cached singleton
    print(settings.database_url)

FastAPI dependency injection::

    from fastapi import Depends
    from app.settings import Settings, get_settings

    def route(settings: Settings = Depends(get_settings)) -> ...:
        ...
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Resolve .env.local paths relative to this module so the app finds the file
# regardless of the process's working directory.
# Priority: repo-root/.env.local < apps/api/.env.local (later wins in pydantic-settings).
# ---------------------------------------------------------------------------
_MODULE_DIR = Path(__file__).parent  # apps/api/app/
_API_DIR = _MODULE_DIR.parent  # apps/api/
_REPO_ROOT = _API_DIR.parent.parent  # ai-plot-twist/

_ENV_FILES: list[str] = [
    str(_REPO_ROOT / ".env.local"),
    str(_API_DIR / ".env.local"),
]


class Settings(BaseSettings):
    """Typed settings loaded from environment / .env.local.

    Fields without a default are **required**: the process will not start
    (or the test will fail loudly) if they are absent.

    R2 credentials are **optional** — any code path that actually calls R2
    will raise lazily if they are not set (spec Edge Case "R2 misconfigured
    in dev").
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App identity ────────────────────────────────────────────────────────
    env: Literal["dev", "prod", "test"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ── Required (no default) ────────────────────────────────────────────────
    database_url: str
    jwt_secret: str

    # ── Optional ─────────────────────────────────────────────────────────────
    # TICK_SECRET is intentionally optional, per the spec edge case:
    # "if the env var is missing, every request to POST /internal/* MUST return
    # 503 and the boot log MUST emit a single warning". The HMAC dependency
    # enforces the 503 at request time (app/middleware/hmac_tick.py).
    tick_secret: str | None = None

    # ── Internal admin / alerting (optional — error at request time if absent) ─
    # ADMIN_TOKEN: Bearer token checked by verify_admin_token (T-016).
    # DISCORD_WEBHOOK_URL: Cycle alert target (T-013, T-014).
    admin_token: str | None = None
    discord_webhook_url: str | None = None

    # ── Module 014 (admin panel) ────────────────────────────────────────────
    # Password for the /admin panel. If unset, POST /admin/auth returns 401.
    admin_password: str | None = None
    # Email address that receives the "winner ready" notification (Resend).
    admin_email: str | None = None
    # Resend API key for the winner notification email.
    resend_api_key: str | None = None

    # ── Cloudflare R2 (optional — safe to leave empty in dev) ───────────────
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    # Public base URL for serving R2 assets (e.g. CDN domain). Required when
    # module 008 wires the real generation_pipeline. Leave empty in dev/test.
    r2_public_base_url: str | None = None

    # ── Module 005 (twists) ─────────────────────────────────────────────────
    # Per FR-004: deleted twists count toward the quota too (anti-spam-then-
    # delete). Override per-env via MAX_TWISTS_PER_USER_PER_CHAPTER.
    max_twists_per_user_per_chapter: int = 3

    # ── Module 007 (voting) ─────────────────────────────────────────────────
    # Cap on how many twists a user can vote for in a chapter (FR-006).
    max_votes_per_user_per_chapter: int = 5
    # Closed-beta default per FR-009: self-voting allowed. Flip to false in
    # prod once the cohort grows past family-and-friends.
    allow_self_vote: bool = True

    # ── Module 006 (director's filter) ──────────────────────────────────────
    # Either or both may be absent in dev/test — main.py logs a warning at
    # boot and leaves the no-op stub registered for ``director_filter`` (so
    # the FSM still cycles through FILTERING without invoking an LLM).
    # When BOTH are set, T-010 wires the real
    # LLMProviderRouter([GeminiProvider, GitHubModelsProvider]).
    gemini_api_key: str | None = None
    github_models_token: str | None = None

    # ── Module 009 (image providers) ────────────────────────────────────────
    # HuggingFace Bearer token for the FLUX.1-schnell fallback. Absent in
    # dev (the dev chain is FakeImageProvider only). When module 008 wires
    # ``chain_for_env("mvp")`` in prod, this MUST be set.
    huggingface_token: str | None = None
    # Per-call generate timeout for the image router. The default favors
    # FLUX.1-schnell on cold start (~60s warmup + ~10s generation).
    t2i_timeout_s: float = 120.0
    # How many retries the router runs on a single provider after
    # ImageProviderUnavailable before falling through to the next one.
    # Initial attempt is NOT counted: max_retries=2 → 3 total attempts.
    t2i_max_retries: int = 2
    # Backoff schedule (seconds) used between retries on the SAME provider.
    # CSV so it can be overridden via env (e.g. "1,3,8"). Indices past the
    # end clamp to the last value.
    t2i_backoff_seconds_csv: str = "2,8"

    # ── Module 012 (video providers) ────────────────────────────────────────
    # Per-call generate timeout for HFVideoProvider (LTX-Video can be slow).
    t2v_timeout_s: float = 300.0
    # How many retries the router runs on a single T2V provider after
    # VideoProviderUnavailable before falling through. Initial attempt NOT
    # counted: max_retries=3 → 4 total attempts per provider.
    t2v_max_retries: int = 3
    # Backoff schedule (seconds) between retries on the SAME T2V provider.
    # CSV so it can be overridden via env (e.g. "5,15,45"). Indices past
    # the end clamp to the last value.
    t2v_backoff_seconds_csv: str = "5,15,45"

    # ── Module 008 (generation pipeline) ───────────────────────────────────
    # Which image-provider chain the pipeline uses. ``dev`` → Fake only;
    # ``mvp`` → Pollinations + HuggingFace (requires huggingface_token).
    generation_image_chain_env: Literal["dev", "mvp"] = "dev"
    # Which video-provider chain the pipeline uses. ``dev`` → Fake only;
    # ``mvp`` → HFVideoProvider + PollinationsVideoProvider.
    generation_video_chain_env: Literal["dev", "mvp"] = "dev"
    # Public URL of the static placeholder image used when a panel fails.
    # Resolved at request time; if unset, the real side-effect stays unwired.
    generation_placeholder_url: str | None = None
    # edge-tts voice for narration. Set to empty string to disable TTS.
    generation_tts_voice: str = "es-AR-ElenaNeural"
    # Max parallel render_panel() calls. Tuned against Fly.io 256 MB RAM:
    # 4 keeps memory under control with FLUX warmup.
    generation_panel_concurrency: int = 4
    # Wall-clock deadline for the panel rendering phase. Panels in flight
    # past this fall back to placeholder.
    generation_deadline_s: float = 600.0
    # Max parallel render_clip() calls in the T2V pipeline. Same default
    # as panel concurrency but kept separate so we can tune the two paths
    # independently.
    generation_clip_concurrency: int = 4
    # Requested duration per T2V clip. The provider may return a slightly
    # different value (LTX-Video rounds to (n*8+1)/fps).
    generation_clip_duration_s: float = 5.0
    # Public URL of the static placeholder mp4 used when a clip fails. Same
    # contract as ``generation_placeholder_url`` but for the T2V path.
    generation_placeholder_video_url: str | None = None
    # When False, the coordinator skips T2V entirely and runs the T2I path.
    # Lets us cut over per-environment without a code change (FR-016 delta).
    video_pipeline_enabled: bool = True
    # Filesystem path of the static placeholder mp4 binary the coordinator
    # uses for individual clip failures. Resolved relative to the repo root
    # in dev and to ``/app/assets/`` in the Docker image. When the file is
    # missing the coordinator falls back to the in-process ``MINIMAL_MP4``
    # sentinel and logs a warning at boot.
    generation_placeholder_video_path: str = "assets/placeholder.mp4"

    # ── Delta 008 — I2V / Layer A (generation pipeline) ────────────────────
    # Public URL of the intro background image (PNG). If None, Layer A is
    # disabled and the pipeline falls through to Layer B (T2V).
    generation_intro_bg_url: str | None = None
    # Public URL of the 2-second outro MP4. If None, Layer A is disabled.
    generation_outro_url: str | None = None
    # Filesystem path of intro_bg.png (used when running locally without R2).
    generation_intro_bg_path: str = "assets/intro_bg.png"
    # Filesystem path of outro.mp4 (used when running locally without R2).
    generation_outro_path: str = "assets/outro.mp4"
    # Duration of the intro clip in seconds (default 2s).
    generation_intro_duration_s: float = 2.0
    # Duration of the outro clip in seconds (default 2s).
    generation_outro_duration_s: float = 2.0
    # drawtext font size for the intro cliffhanger overlay.
    generation_intro_font_size: int = 64
    # drawtext font color for the intro cliffhanger overlay.
    generation_intro_font_color: str = "white"
    # Kling API key for the I2V provider (Delta 012). When absent the pipeline
    # uses FakeImageToVideoProvider (no-op, returns placeholder bytes).
    kling_api_key: str | None = None

    # ── Module 011 (web push) ──────────────────────────────────────────────
    # VAPID identity. Both required for the real fan-out side-effect.
    # Missing either keeps the no-op stub registered so the FSM still
    # transitions through ESTRENO cleanly.
    vapid_private_key: str | None = None
    vapid_public_key: str | None = None
    vapid_subject: str = "mailto:operator@aiplottwist.example"
    # Wall-clock deadline for one fan-out across all subscriptions.
    # Beyond this any in-flight send is cancelled and the row gets
    # left untouched for the next tick.
    push_fanout_timeout_s: float = 60.0
    # Bounded parallel sends per fan-out (asyncio.Semaphore size).
    push_fanout_concurrency: int = 8
    # Subscriptions with failure_count >= threshold AND no recent
    # success are culled at the end of every fan-out (R-005).
    push_failure_threshold: int = 3

    # ── Derived helpers ──────────────────────────────────────────────────────

    @property
    def log_level_int(self) -> int:
        """Numeric log level for the stdlib ``logging`` module.

        Uses ``logging.getLevelNamesMapping()`` (Python 3.11+) for clean typing.
        """
        # getLevelNamesMapping() is available from Python 3.11; returns dict[str, int].
        return logging.getLevelNamesMapping()[self.log_level]

    @property
    def is_dev(self) -> bool:
        """True when running in local development."""
        return self.env == "dev"

    @property
    def is_test(self) -> bool:
        """True when running under pytest."""
        return self.env == "test"

    @property
    def t2v_backoff_seconds(self) -> tuple[float, ...]:
        """Parse ``t2v_backoff_seconds_csv`` into the tuple the router expects."""
        try:
            parts = [
                float(s.strip())
                for s in self.t2v_backoff_seconds_csv.split(",")
                if s.strip()
            ]
        except ValueError:
            parts = []
        return tuple(parts) if parts else (5.0, 15.0, 45.0)

    @property
    def t2i_backoff_seconds(self) -> tuple[float, ...]:
        """Parse ``t2i_backoff_seconds_csv`` into the tuple the router expects.

        Empty or malformed entries fall back to ``(2.0, 8.0)`` rather than
        raising so a misconfigured env var doesn't crash boot.
        """
        try:
            parts = [
                float(s.strip())
                for s in self.t2i_backoff_seconds_csv.split(",")
                if s.strip()
            ]
        except ValueError:
            parts = []
        return tuple(parts) if parts else (2.0, 8.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application ``Settings`` singleton (cached after first call).

    The cache is intentional: settings are immutable once the process starts.
    In tests, call ``get_settings.cache_clear()`` before each test that changes
    env vars, or instantiate ``Settings(...)`` directly.
    """
    # pydantic-settings loads required fields from environment variables at runtime.
    # mypy cannot verify this statically — the type: ignore is documented and safe.
    return Settings()  # type: ignore[call-arg]
