"""DI registry for cycle side effects + module-003 stub implementations.

Module 003 / Task T-006.

The registry decouples the executor from concrete side-effect implementations.
Modules 006 (director filter) and 008 (generation pipeline) override the
stubs registered here by calling ``register()`` at startup.

Usage (executor side)::

    from app.domain.side_effects import get

    fn = get("director_filter")
    await fn(chapter_id)

Usage (module 006 / 008 override)::

    from app.domain import side_effects

    side_effects.register("director_filter", real_director_filter)

The stubs registered here are no-ops that log their intent.  They allow the
FSM loop to be exercised end-to-end before the real LLM-backed implementations
are available.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

#: A side-effect function receives the chapter_id and returns nothing.
SideEffect = Callable[[int], Awaitable[None]]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, SideEffect] = {}


def register(name: str, fn: SideEffect) -> None:
    """Register (or replace) a side-effect implementation.

    Thread-safe for reads; writes should happen only at startup (module import
    time).  Calling ``register`` after the first request is processed is
    supported but not recommended.

    Args:
        name: Logical name used by the executor (e.g. ``"director_filter"``).
        fn: Async callable receiving ``chapter_id: int``.
    """
    _registry[name] = fn
    logger.debug("side_effect.registered name=%s impl=%s", name, fn.__qualname__)


def get(name: str) -> SideEffect:
    """Return the registered side-effect implementation for *name*.

    Args:
        name: Logical name (must have been registered via ``register()``).

    Raises:
        KeyError: *name* has not been registered.
    """
    try:
        return _registry[name]
    except KeyError:
        registered = list(_registry)
        raise KeyError(
            f"No side effect registered for {name!r}. "
            f"Registered: {registered}"
        ) from None


# ---------------------------------------------------------------------------
# Module-003 stub implementations
# ---------------------------------------------------------------------------


async def director_filter_stub(chapter_id: int) -> None:
    """Stub: no-op director-filter side effect.

    Logs that it would run, then returns immediately.
    The real implementation (module 006) updates ``twists.status``
    and transitions the cycle to VOTACION.
    """
    logger.info(
        "director_filter_stub chapter_id=%d  "
        "(stub — real impl injected by module 006)",
        chapter_id,
    )


async def generation_pipeline_stub(chapter_id: int) -> None:
    """Stub: no-op generation-pipeline side effect.

    Logs that it would run, then returns immediately.
    The real implementation (module 008) clones the chapter manifest,
    inserts the next chapter row, and transitions the cycle to
    PENDING_RELEASE.
    """
    logger.info(
        "generation_pipeline_stub chapter_id=%d  "
        "(stub — real impl injected by module 008)",
        chapter_id,
    )


# Register stubs at import time.
# Modules 006/008 override these by calling register() during their own init.
register("director_filter", director_filter_stub)
register("generation_pipeline", generation_pipeline_stub)
