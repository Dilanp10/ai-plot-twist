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

    # ── Cloudflare R2 (optional — safe to leave empty in dev) ───────────────
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None

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
