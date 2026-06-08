"""Request-ID middleware.

Generates (or preserves a client-provided) ``X-Request-Id`` header for every
HTTP request, binds it to structlog's contextvars so every log inside the
request scope carries it, and echoes it back in the response.

Spec FR-008 / constitution Gate 10: every request must be traceable across
logs without manual instrumentation by route handlers.

Why a fresh ``uuid4`` instead of a counter? Counters leak load information
and are not safe across multiple replicas. UUIDs are collision-free without
coordination.

Why honor an upstream ``X-Request-Id``? Future modules will sit behind a
Cloudflare-aware edge layer that may emit its own request id; respecting it
preserves traces end-to-end.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a UUIDv4 ``request_id`` into the request context and response headers."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        # Bind to structlog contextvars so every log call within this request
        # carries ``request_id=...`` automatically. Each request runs in its own
        # asyncio task, so this binding does not leak across concurrent requests.
        # We still unbind explicitly on the way out to keep the contextvars clean
        # if the calling task is reused (defensive).
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
