"""Tests for application settings loading.

Module 001 / Task T-005.

Coverage:
  (a) All optional fields have correct defaults when required fields are provided.
  (b) Missing required fields raise ``ValidationError`` at construction time.
  (c) Environment variables override defaults.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.settings import Settings

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make(
    *,
    database_url: str = "postgresql+asyncpg://app:app@localhost:5433/test",
    tick_secret: str = "test-tick-secret",
    jwt_secret: str = "test-jwt-secret",
    env: str = "dev",
    log_level: str = "INFO",
    r2_account_id: str | None = None,
    r2_access_key_id: str | None = None,
    r2_secret_access_key: str | None = None,
    r2_bucket: str | None = None,
) -> Settings:
    """Construct Settings without loading any .env file.

    Required fields have safe test defaults; override any of them as needed.
    The ``type: ignore[call-arg]`` below is intentional: pydantic-settings
    accepts ``_env_file=None`` as an init-time override to suppress file
    loading; mypy cannot see this undocumented-but-stable kwarg.
    """
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        tick_secret=tick_secret,
        jwt_secret=jwt_secret,
        env=env,  # type: ignore[arg-type]
        log_level=log_level,  # type: ignore[arg-type]
        r2_account_id=r2_account_id,
        r2_access_key_id=r2_access_key_id,
        r2_secret_access_key=r2_secret_access_key,
        r2_bucket=r2_bucket,
    )


# ---------------------------------------------------------------------------
# (a) Defaults
# ---------------------------------------------------------------------------


def test_defaults() -> None:
    """All optional fields have correct defaults when required fields are set."""
    s = _make()

    assert s.env == "dev"
    assert s.log_level == "INFO"
    assert s.r2_account_id is None
    assert s.r2_access_key_id is None
    assert s.r2_secret_access_key is None
    assert s.r2_bucket is None


def test_log_level_int_maps_correctly() -> None:
    """log_level_int returns the correct stdlib logging integer."""
    assert _make(log_level="DEBUG").log_level_int == logging.DEBUG
    assert _make(log_level="INFO").log_level_int == logging.INFO
    assert _make(log_level="WARNING").log_level_int == logging.WARNING
    assert _make(log_level="ERROR").log_level_int == logging.ERROR
    assert _make(log_level="CRITICAL").log_level_int == logging.CRITICAL


def test_is_dev_and_is_test_flags() -> None:
    """Convenience properties reflect the env field."""
    assert _make(env="dev").is_dev is True
    assert _make(env="dev").is_test is False
    assert _make(env="test").is_test is True
    assert _make(env="test").is_dev is False
    assert _make(env="prod").is_dev is False
    assert _make(env="prod").is_test is False


def test_r2_optional_fields_accept_values() -> None:
    """R2 fields accept string values when provided."""
    s = _make(
        r2_account_id="acct-123",
        r2_access_key_id="key-id",
        r2_secret_access_key="secret",
        r2_bucket="my-bucket",
    )

    assert s.r2_account_id == "acct-123"
    assert s.r2_access_key_id == "key-id"
    assert s.r2_secret_access_key == "secret"
    assert s.r2_bucket == "my-bucket"


# ---------------------------------------------------------------------------
# (b) Missing required raises
# ---------------------------------------------------------------------------


def test_missing_required_raises() -> None:
    """Missing required fields raise ValidationError at instantiation."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    errors = exc_info.value.errors()
    missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
    assert "database_url" in missing_fields
    assert "tick_secret" in missing_fields
    assert "jwt_secret" in missing_fields


# ---------------------------------------------------------------------------
# (c) Environment variable override
# ---------------------------------------------------------------------------


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables override defaults and supply required fields."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://override:5432/db")
    monkeypatch.setenv("TICK_SECRET", "override-tick")
    monkeypatch.setenv("JWT_SECRET", "override-jwt")
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("R2_BUCKET", "my-r2-bucket")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.database_url == "postgresql+asyncpg://override:5432/db"
    assert s.tick_secret == "override-tick"
    assert s.jwt_secret == "override-jwt"
    assert s.env == "prod"
    assert s.log_level == "DEBUG"
    assert s.r2_bucket == "my-r2-bucket"
    assert s.r2_account_id is None  # unset R2 fields remain None
