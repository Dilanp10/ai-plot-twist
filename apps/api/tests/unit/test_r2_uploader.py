"""Unit tests: R2Uploader (mock-based).

Module 008 / Task T-006.

All tests use a mock boto3 client — no real R2 credentials needed.
The mock is injected by replacing ``uploader._client`` directly after
construction (avoids patching boto3.client at module level).

Coverage:
  - Successful upload returns the correct public URL.
  - Cache-Control header is set on every put_object call.
  - ContentType is forwarded verbatim.
  - 5xx on first attempt → retried; succeeds on second → returns URL.
  - 5xx on all 3 retries + initial attempt → raises R2UploadError.
  - Non-5xx ClientError (e.g. 403) → raised immediately, no retry.
  - Retry sleeps use the [1, 3, 9] backoff schedule.
  - Empty body uploads succeed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from botocore.exceptions import ClientError

from app.infra.r2_uploader import R2Uploader, R2UploadError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCOUNT_ID = "test-account"
_KEY_ID = "test-key-id"
_SECRET = "test-secret"
_BUCKET = "test-bucket"
_BASE_URL = "https://assets.example.com"


def _client_error(status: int, code: str = "InternalError") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test error"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


def _make_uploader(mock_client: MagicMock) -> R2Uploader:
    """Create an R2Uploader with the boto3 client replaced by a mock."""
    with patch("boto3.client", return_value=mock_client):
        uploader = R2Uploader(
            account_id=_ACCOUNT_ID,
            key_id=_KEY_ID,
            secret=_SECRET,
            bucket=_BUCKET,
            public_base_url=_BASE_URL,
        )
    return uploader


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_success_returns_public_url() -> None:
    mock_client = MagicMock()
    mock_client.put_object.return_value = {}
    uploader = _make_uploader(mock_client)

    url = await uploader.upload("seasons/s01/ch01/1-abc.webp", b"bytes", "image/webp")

    assert url == "https://assets.example.com/seasons/s01/ch01/1-abc.webp"


@pytest.mark.asyncio
async def test_upload_sets_cache_control() -> None:
    mock_client = MagicMock()
    mock_client.put_object.return_value = {}
    uploader = _make_uploader(mock_client)

    await uploader.upload("key.webp", b"x", "image/webp")

    _, kwargs = mock_client.put_object.call_args
    assert kwargs["CacheControl"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_upload_sets_content_type() -> None:
    mock_client = MagicMock()
    mock_client.put_object.return_value = {}
    uploader = _make_uploader(mock_client)

    await uploader.upload("audio.mp3", b"mp3bytes", "audio/mpeg")

    _, kwargs = mock_client.put_object.call_args
    assert kwargs["ContentType"] == "audio/mpeg"


@pytest.mark.asyncio
async def test_upload_sets_bucket_and_key() -> None:
    mock_client = MagicMock()
    mock_client.put_object.return_value = {}
    uploader = _make_uploader(mock_client)

    await uploader.upload("my/key.webp", b"data", "image/webp")

    _, kwargs = mock_client.put_object.call_args
    assert kwargs["Bucket"] == _BUCKET
    assert kwargs["Key"] == "my/key.webp"
    assert kwargs["Body"] == b"data"


@pytest.mark.asyncio
async def test_upload_retries_on_503_and_succeeds() -> None:
    mock_client = MagicMock()
    mock_client.put_object.side_effect = [
        _client_error(503),
        {},  # success on 2nd attempt
    ]
    uploader = _make_uploader(mock_client)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        url = await uploader.upload("k.webp", b"x", "image/webp")

    assert url == f"{_BASE_URL}/k.webp"
    assert mock_client.put_object.call_count == 2
    mock_sleep.assert_called_once_with(1.0)  # first backoff


@pytest.mark.asyncio
async def test_upload_retries_all_three_times_then_raises() -> None:
    mock_client = MagicMock()
    mock_client.put_object.side_effect = [
        _client_error(500),
        _client_error(503),
        _client_error(502),
        _client_error(500),  # 4th attempt also fails
    ]
    uploader = _make_uploader(mock_client)

    with (
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        pytest.raises(R2UploadError),
    ):
        await uploader.upload("k.webp", b"x", "image/webp")

    assert mock_client.put_object.call_count == 4
    # backoffs: 1, 3, 9
    assert mock_sleep.call_args_list == [call(1.0), call(3.0), call(9.0)]


@pytest.mark.asyncio
async def test_upload_non_5xx_raises_immediately() -> None:
    """403 Forbidden must NOT be retried — it's an operator config error."""
    mock_client = MagicMock()
    mock_client.put_object.side_effect = _client_error(403, "AccessDenied")
    uploader = _make_uploader(mock_client)

    with (
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        pytest.raises(ClientError) as exc_info,
    ):
        await uploader.upload("k.webp", b"x", "image/webp")

    assert mock_client.put_object.call_count == 1  # no retry
    mock_sleep.assert_not_called()
    assert exc_info.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403


@pytest.mark.asyncio
async def test_upload_empty_body_succeeds() -> None:
    mock_client = MagicMock()
    mock_client.put_object.return_value = {}
    uploader = _make_uploader(mock_client)

    url = await uploader.upload("placeholder.webp", b"", "image/webp")

    assert url.endswith("placeholder.webp")
    _, kwargs = mock_client.put_object.call_args
    assert kwargs["Body"] == b""


@pytest.mark.asyncio
async def test_public_base_url_trailing_slash_stripped() -> None:
    mock_client = MagicMock()
    mock_client.put_object.return_value = {}
    with patch("boto3.client", return_value=mock_client):
        uploader = R2Uploader(
            account_id="acc",
            key_id="k",
            secret="s",
            bucket="b",
            public_base_url="https://assets.example.com/",  # trailing slash
        )

    url = await uploader.upload("file.webp", b"x", "image/webp")

    assert url == "https://assets.example.com/file.webp"
    assert "//" not in url.replace("https://", "")
