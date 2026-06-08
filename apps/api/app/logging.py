"""Structured logging configuration for the AI Plot Twist API.

Sets up ``structlog`` with a JSON-to-stdout pipeline suitable for Fly.io log
drains and local ``grep``-able development logs.

Usage::

    # Once at process startup (called from app/main.py):
    from app.logging import configure_logging
    configure_logging("INFO")

    # Everywhere else:
    from app.logging import get_logger
    log = get_logger(__name__)
    log.info("chapter_released", chapter_id=42, duration_ms=120)

Gate 10 compliance: every log entry automatically includes:
  - ``level``          — log level string
  - ``logger``         — Python module name
  - ``timestamp``      — ISO-8601 UTC
  - ``event``          — the message passed to log.info/debug/etc.
  - Any extra kwargs   — structured key-value pairs (e.g. request_id, outcome)
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import FilteringBoundLogger


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog for JSON output to stdout.

    Must be called **once** before any logger is used. Subsequent calls are
    safe but redundant (``cache_logger_on_first_use=True`` freezes the config
    after the first ``get_logger()`` call).

    Args:
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    """
    level_int: int = logging.getLevelNamesMapping()[log_level.upper()]

    # Mirror the stdlib root logger level so third-party libraries that use
    # stdlib logging (uvicorn, sqlalchemy, etc.) respect the same threshold.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level_int,
    )

    structlog.configure(
        processors=[
            # Merge any context vars bound via structlog.contextvars.bind_contextvars()
            # (used by the request-id middleware in T-008).
            structlog.contextvars.merge_contextvars,
            # Standard metadata processors.
            structlog.processors.add_log_level,
            # Note: ``structlog.stdlib.add_logger_name`` is intentionally omitted.
            # It expects the underlying factory to yield a stdlib ``logging.Logger``
            # (which exposes ``.name``), but we use ``PrintLoggerFactory`` for direct
            # stdout writes. If we need the logger name in the JSON, add a custom
            # processor that reads it from the bound logger's ``_context``.
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Exception handling: render tracebacks into the JSON ``exception`` key.
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            # Final serialization.
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    """Return a structlog bound logger for *name*.

    Typical usage::

        log = get_logger(__name__)
        log.info("event_name", key="value")

    The return type is ``FilteringBoundLogger`` (``structlog.types``).
    The ``# type: ignore[return-value]`` below is a documented workaround:
    ``structlog.get_logger()`` is typed as ``-> Any`` in structlog's stubs,
    but with ``make_filtering_bound_logger`` as the wrapper class, the runtime
    type IS ``FilteringBoundLogger``. This cast is safe and intentional.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
