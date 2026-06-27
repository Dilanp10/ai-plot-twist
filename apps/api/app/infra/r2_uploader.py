"""R2Uploader — async wrapper around boto3 for Cloudflare R2 uploads.

Module 008 / Task T-006.

Uses boto3's S3-compatible client pointed at R2, wrapped in
``asyncio.get_running_loop().run_in_executor`` so the synchronous boto3
call does not block the event loop (research R-007).

Retry policy: 3 retries on HTTP 5xx with backoff [1, 3, 9] seconds.
Non-5xx ``ClientError`` (e.g. 403 Forbidden, 404 Not Found) are raised
immediately — they indicate operator error, not transient failures.

Cache-Control is set to ``public, max-age=31536000, immutable`` on every
object so CDN and browser caches treat R2 assets as perpetual (content is
content-addressed via sha256 suffix per module 009 path scheme).
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_RETRY_BACKOFF: tuple[float, ...] = (1.0, 3.0, 9.0)
_CACHE_CONTROL = "public, max-age=31536000, immutable"


class R2UploadError(Exception):
    """Raised when all retry attempts for an R2 upload are exhausted."""


class R2Uploader:
    """Async S3-compatible uploader for Cloudflare R2.

    Parameters
    ----------
    account_id:
        Cloudflare account ID (used to build the endpoint URL).
    key_id:
        R2 access key ID.
    secret:
        R2 secret access key.
    bucket:
        Target R2 bucket name.
    public_base_url:
        Base URL for public asset access (e.g.
        ``https://assets.aiplottwist.example``). Trailing slash is stripped.
    """

    def __init__(
        self,
        account_id: str,
        key_id: str,
        secret: str,
        bucket: str,
        public_base_url: str,
    ) -> None:
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name="auto",
        )

    def generate_presigned_put_url(
        self,
        key: str,
        expires_in: int = 900,
        content_type: str = "video/mp4",
    ) -> tuple[str, str]:
        """Return ``(upload_url, public_url)`` for a direct browser PUT to R2.

        ``upload_url`` is a presigned S3 PUT URL valid for *expires_in* seconds
        (default 15 min). The caller gives it to the browser, which PUTs the
        file directly — no bytes pass through the API server.

        ``public_url`` is the permanent CDN URL the object will have once
        uploaded (``{public_base_url}/{key}``).

        ``generate_presigned_url`` is a local crypto operation (no network
        call), so no executor is needed.
        """
        upload_url: str = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
        public_url = f"{self._public_base_url}/{key}"
        return upload_url, public_url

    async def upload(self, key: str, body: bytes, content_type: str) -> str:
        """Upload *body* to R2 at *key* and return its public URL.

        Retries up to 3 times on HTTP 5xx with backoff ``[1, 3, 9]`` s.
        Raises :exc:`R2UploadError` when all attempts are exhausted.
        Raises :exc:`botocore.exceptions.ClientError` immediately on
        non-5xx errors (auth failure, bucket not found, etc.).

        Parameters
        ----------
        key:
            Object key inside the bucket (e.g.
            ``seasons/s01/abc123/1-a1b2c3d4.webp``).
        body:
            Raw bytes to upload.
        content_type:
            MIME type (``image/webp``, ``audio/mpeg``, …).

        Returns
        -------
        str
            Public URL: ``{public_base_url}/{key}``.
        """
        loop = asyncio.get_running_loop()
        put = partial(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl=_CACHE_CONTROL,
        )
        last_exc: ClientError | None = None

        for attempt in range(1 + len(_RETRY_BACKOFF)):
            try:
                await loop.run_in_executor(None, put)
                if attempt > 0:
                    logger.info(
                        "r2_upload_succeeded_after_retry key=%s attempt=%d",
                        key,
                        attempt,
                    )
                return f"{self._public_base_url}/{key}"

            except ClientError as exc:
                status = (
                    exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                )
                if status >= 500:
                    last_exc = exc
                    if attempt < len(_RETRY_BACKOFF):
                        backoff = _RETRY_BACKOFF[attempt]
                        logger.warning(
                            "r2_upload_5xx key=%s status=%d attempt=%d backoff=%.0fs",
                            key,
                            status,
                            attempt,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    break  # retries exhausted → fall through to R2UploadError
                raise  # non-5xx: bubble immediately

        raise R2UploadError(
            f"R2 upload failed after {len(_RETRY_BACKOFF)} retries: {key}"
        ) from last_exc
