"""RFC 7807 Problem Details — exception type + global handler.

All endpoints raise :class:`ProblemDetail` for structured error responses.
The handler registered in :func:`app.main.create_app` converts these to an
``application/problem+json`` response with the documented body shape::

    {
      "type":     "about:blank",
      "title":    "<short, human-readable>",
      "status":   <int>,
      "code":     "<stable machine-readable id>",
      "detail":   "<optional longer message>",
      "instance": "<request path>"
    }

Why introduce this in T-011 instead of T-010? T-010's ``/healthz`` uses a
plain ``{status, checks}`` envelope per its own contract (not RFC 7807).
T-011 is the first task whose contract mandates ``application/problem+json``,
so the helper lands here and is reused by every future module.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ProblemDetail(Exception):
    """Structured RFC 7807 error.

    ``code`` is the **stable machine-readable identifier** that clients
    branch on (e.g. ``"bad_hmac"``, ``"ts_drift"``). ``title`` is short and
    user-facing. ``detail`` is optional and may include more context, but
    MUST NOT contain secrets, stack traces, or PII.
    """

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str = "",
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        super().__init__(detail or title)


async def problem_handler(request: Request, exc: Exception) -> JSONResponse:
    """Convert a :class:`ProblemDetail` into a Problem+JSON response.

    The handler signature receives ``Exception`` (FastAPI's runtime contract);
    we narrow to ``ProblemDetail`` with ``isinstance`` so mypy strict is happy.
    """
    assert isinstance(exc, ProblemDetail), (
        "problem_handler should only be registered for ProblemDetail"
    )
    body: dict[str, object] = {
        "type": "about:blank",
        "title": exc.title,
        "status": exc.status,
        "code": exc.code,
        "instance": str(request.url.path),
    }
    if exc.detail:
        body["detail"] = exc.detail
    return JSONResponse(
        status_code=exc.status,
        content=body,
        media_type="application/problem+json",
    )
