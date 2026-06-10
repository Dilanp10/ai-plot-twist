"""HTTP cache-header helpers for the content endpoints.

Module 004 / Task T-006.

Centralizes the ``Cache-Control`` and ``ETag`` writing logic so every handler
in module 004 (and future read modules) emits byte-identical headers. Three
recipes covered by spec FR-007:

  * ``/chapters/today``               — short fresh + long swr + must-revalidate.
  * ``/chapters/{id}`` archived       — long, ``immutable``.
  * ``/chapters/{id}`` live           — short fresh + long swr.
  * ``/seasons/{slug}``               — medium fresh + long swr.
  * 503 (kill-switch / no-season)     — ``no-store``.

``set_etag`` adds RFC 7232 §2.3 surrounding double quotes; the bare hex from
:func:`app.domain.etag.derive_etag` is never emitted unquoted.
"""

from __future__ import annotations

from fastapi import Response


def set_cache(
    response: Response,
    *,
    max_age: int,
    swr: int = 0,
    immutable: bool = False,
    must_revalidate: bool = False,
    no_store: bool = False,
) -> None:
    """Write ``Cache-Control`` on *response* with the directives configured.

    Parameters
    ----------
    response:
        FastAPI/Starlette response object.
    max_age:
        ``max-age=N`` in seconds.
    swr:
        ``stale-while-revalidate=N`` in seconds. Omitted from the header when 0.
    immutable:
        Add the ``immutable`` directive (RFC 8246) for assets that never change.
    must_revalidate:
        Add ``must-revalidate`` so clients cannot serve stale forever when
        the network is reachable (RFC 9111 §5.2.2.2).
    no_store:
        When ``True``, the header becomes ``no-store`` and every other
        parameter is ignored (RFC 9111 §5.2.2.5). Use for error responses
        that must not be cached at any layer.
    """
    if no_store:
        response.headers["Cache-Control"] = "no-store"
        return

    parts: list[str] = ["public", f"max-age={max_age}"]
    if swr > 0:
        parts.append(f"stale-while-revalidate={swr}")
    if must_revalidate:
        parts.append("must-revalidate")
    if immutable:
        parts.append("immutable")
    response.headers["Cache-Control"] = ", ".join(parts)


def set_etag(response: Response, etag_hex: str) -> None:
    """Write ``ETag`` on *response* with RFC 7232 §2.3 surrounding quotes.

    The input is the bare 16-char hex from
    :func:`app.domain.etag.derive_etag`. This helper is the **only** place
    the quotes are added so handlers never accidentally double-quote.
    """
    response.headers["ETag"] = f'"{etag_hex}"'
