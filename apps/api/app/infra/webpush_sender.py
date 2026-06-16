"""WebPushSender — async-friendly wrapper around pywebpush (T-004).

Module 011 / Task T-004.

``pywebpush.webpush`` is a blocking ``requests``-based call. We push
it through ``loop.run_in_executor`` so the fan-out (T-006) can drive
many concurrent sends from a single asyncio coroutine without blocking
the event loop.

Result translation (FR-008 sub-bullets):
  - 201 / 204                → ``SendResult.success``
  - 404 / 410                → ``SendResult.gone``   (delete the row)
  - everything else / raise  → ``SendResult.failed`` (increment counter)

The Gone path includes 404 because some push services (notably old
FCM endpoints) return 404 for a permanently revoked subscription —
the SDK's ``WebPushException`` carries the response status code so
we read it before falling through to ``failed``.

``vapid_subject`` is the ``mailto:`` URL push services REQUIRE so they
can contact us out-of-band about abuse. It must match the VAPID JWT's
``sub`` claim; we hand it as a setting (``VAPID_SUBJECT``,
``mailto:admin@aiplottwist.example``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Any

from pywebpush import WebPushException, webpush

if TYPE_CHECKING:  # pragma: no cover
    from app.infra.push_subscriptions_repo import Subscription

logger = logging.getLogger(__name__)

# pywebpush surfaces network errors as plain Exception inside requests.
# Anything we don't recognise → failed (per FR-008).


class SendResult(StrEnum):
    """Outcome of a single ``WebPushSender.send`` call."""

    SUCCESS = "success"
    GONE = "gone"
    FAILED = "failed"


@dataclass(frozen=True)
class SendOutcome:
    """Per-send result the orchestrator hands to the repo for accounting.

    Keeping the subscription id alongside the result keeps the
    fan-out's bulk-update step type-safe (one outcome → one mark_*
    call without needing to zip parallel lists).
    """

    subscription_id: int
    result: SendResult
    status_code: int | None = None
    error: str | None = None


_GONE_STATUSES: frozenset[int] = frozenset({404, 410})
_SUCCESS_STATUSES: frozenset[int] = frozenset({201, 204})


class WebPushSender:
    """Async wrapper over ``pywebpush.webpush`` for fan-out orchestration."""

    def __init__(
        self,
        *,
        vapid_private_key: str,
        vapid_subject: str,
    ) -> None:
        """Build a reusable sender bound to one VAPID identity.

        Parameters
        ----------
        vapid_private_key:
            PKCS#8 PEM exactly as generate-vapid (T-002) emits. The
            JWT signing happens per-call inside pywebpush.
        vapid_subject:
            ``mailto:operator@example.com``. Must match the JWT ``sub``
            claim — push services reject mismatched JWTs.
        """
        # Some secret stores (Fly.io secrets set via single-line CLI args)
        # collapse PEM newlines to the literal two-char sequence "\n".
        # Normalize back to real newlines so pywebpush/cryptography accepts it.
        self._private_key = vapid_private_key.replace("\\n", "\n")
        self._claims: dict[str, str] = {"sub": vapid_subject}

    async def send(
        self,
        subscription: Subscription,
        payload: bytes | dict[str, Any],
        *,
        timeout: float = 10.0,
    ) -> SendOutcome:
        """Send ``payload`` to one subscription and translate the result.

        Wraps the blocking ``pywebpush.webpush`` call in
        ``run_in_executor``. The default ``timeout=10`` matches
        pywebpush's per-call requests timeout — past that the call
        raises and is bucketed as ``FAILED`` (the orchestrator may
        retry on a later tick).

        The payload can be a JSON-ready dict (we serialise it here so
        the caller doesn't accidentally pass differing encodings to
        different subscriptions in the same fan-out) or pre-encoded
        bytes when the caller has already serialised once.
        """
        body_bytes: bytes = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

        loop = asyncio.get_running_loop()
        sub_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh_key,
                "auth": subscription.auth_key,
            },
        }
        call = partial(
            webpush,
            subscription_info=sub_info,
            data=body_bytes,
            vapid_private_key=self._private_key,
            vapid_claims=dict(self._claims),
            timeout=timeout,
        )

        try:
            response = await loop.run_in_executor(None, call)
        except WebPushException as exc:
            status = (
                exc.response.status_code if exc.response is not None else None
            )
            if status is not None and status in _GONE_STATUSES:
                logger.info(
                    "push_subscription_gone subscription_id=%d status=%d",
                    subscription.id,
                    status,
                )
                return SendOutcome(
                    subscription_id=subscription.id,
                    result=SendResult.GONE,
                    status_code=status,
                )
            logger.warning(
                "push_send_failed subscription_id=%d status=%s error=%s",
                subscription.id,
                status,
                exc,
            )
            return SendOutcome(
                subscription_id=subscription.id,
                result=SendResult.FAILED,
                status_code=status,
                error=str(exc),
            )
        except Exception as exc:
            logger.warning(
                "push_send_failed_unexpected subscription_id=%d error=%s",
                subscription.id,
                exc,
            )
            return SendOutcome(
                subscription_id=subscription.id,
                result=SendResult.FAILED,
                error=str(exc),
            )

        status = getattr(response, "status_code", None)
        if status in _SUCCESS_STATUSES:
            logger.info(
                "push_sent subscription_id=%d status=%d",
                subscription.id,
                status,
            )
            return SendOutcome(
                subscription_id=subscription.id,
                result=SendResult.SUCCESS,
                status_code=status,
            )
        if status in _GONE_STATUSES:
            logger.info(
                "push_subscription_gone subscription_id=%d status=%d",
                subscription.id,
                status,
            )
            return SendOutcome(
                subscription_id=subscription.id,
                result=SendResult.GONE,
                status_code=status,
            )
        logger.warning(
            "push_send_failed subscription_id=%d status=%s",
            subscription.id,
            status,
        )
        return SendOutcome(
            subscription_id=subscription.id,
            result=SendResult.FAILED,
            status_code=status,
        )
