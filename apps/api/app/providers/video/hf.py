"""HFVideoProvider — text-to-video via HF Inference API (LTX-Video).

Module 012 / Task T-003.

Uses ``Lightricks/LTX-Video`` via the HF Inference API. The API returns
raw MP4 bytes on 200; duration is validated by walking the mvhd box before
returning. We parse mvhd (Movie Header Box) directly via struct because
LTX-Video produces video-only clips (no audio track), and mutagen reads
duration from the audio track's mdhd — which is absent in video-only files.

Cold-start handling
-------------------
HF returns ``HTTP 503`` with JSON containing ``"estimated_time"`` when the
model is loading. We map this to :class:`VideoProviderUnavailable` so the
router retries with backoff.

Exception mapping (router policy lives in :class:`VideoProviderRouter`):

  HTTP 429                             → ``VideoProviderRateLimited``
  HTTP 5xx / cold-start 503 / timeout  → ``VideoProviderUnavailable``
  Empty body / corrupt MP4 / short clip → ``VideoProviderInvalidOutput``
  HTTP 401 / 403 / other 4xx           → ``VideoProviderError``
"""

from __future__ import annotations

import struct
import time
from typing import Any

import httpx

from app.providers.video.base import (
    VideoProvider,
    VideoProviderError,
    VideoProviderInvalidOutput,
    VideoProviderRateLimited,
    VideoProviderUnavailable,
    VideoRequest,
    VideoResult,
)

_BASE_URL = "https://api-inference.huggingface.co"
_ENDPOINT = f"{_BASE_URL}/models/Lightricks/LTX-Video"
_MODEL = "ltx-video"
_HEALTH_TIMEOUT_S = 2.0
_DEFAULT_GENERATE_TIMEOUT_S = 300.0


# ---------------------------------------------------------------------------
# Helpers (exported so tests can exercise them in isolation)
# ---------------------------------------------------------------------------


def _derive_num_frames(duration_s: float, fps: int) -> int:
    """Return the nearest LTX-Video-valid frame count for the given duration.

    LTX-Video enforces ``num_frames % 8 == 1``.
    Formula: ``n = max(1, round((raw-1)/8)); return n*8+1``.

    Examples
    --------
    >>> _derive_num_frames(5.0, 24)   # raw=120 → n=15 → 121
    121
    >>> _derive_num_frames(1.0, 24)   # raw=24  → n=3  → 25
    25
    """
    raw = duration_s * fps
    n = max(1, round((raw - 1) / 8))
    return n * 8 + 1


def _parse_mp4_duration(data: bytes) -> float:
    """Parse MP4 clip duration (seconds) by walking the mvhd box.

    LTX-Video produces video-only clips (no audio track). We parse the
    Movie Header Box (mvhd) directly via struct so the check works for
    video-only MP4s. mutagen is intentionally NOT used here because it
    reads duration from the audio track's mdhd box, which is absent.

    Raises
    ------
    VideoProviderInvalidOutput
        If ``data`` is not parseable as MP4 or the mvhd box is missing.
    """
    try:
        return _find_mvhd_duration(data, 0, len(data))
    except (struct.error, ValueError) as exc:
        raise VideoProviderInvalidOutput(
            f"hf: corrupt MP4 or missing mvhd: {exc!r}"
        ) from exc


def _find_mvhd_duration(data: bytes, start: int, end: int) -> float:
    """Walk ISO-BMFF boxes in ``data[start:end]`` to find and read mvhd."""
    pos = start
    while pos + 8 <= end:
        box_size = struct.unpack_from(">I", data, pos)[0]
        box_type = data[pos + 4 : pos + 8]

        if box_size == 1:
            # 64-bit extended size
            if pos + 16 > end:
                raise ValueError("truncated 64-bit size field")
            box_size = struct.unpack_from(">Q", data, pos + 8)[0]
            payload_start = pos + 16
        elif box_size == 0:
            # box extends to EOF
            box_size = end - pos
            payload_start = pos + 8
        else:
            payload_start = pos + 8

        if box_size < 8:
            raise ValueError(f"invalid box size {box_size} at offset {pos}")

        if box_type == b"moov":
            return _find_mvhd_duration(data, payload_start, pos + box_size)

        if box_type == b"mvhd":
            # FullBox: 1 byte version + 3 bytes flags, then fields
            version = data[payload_start]
            fields_start = payload_start + 4  # skip version + flags
            timescale: int
            duration: int
            if version == 0:
                # creation_time(u32) modification_time(u32) timescale(u32) duration(u32)
                raw = struct.unpack_from(">IIII", data, fields_start)
                timescale, duration = int(raw[2]), int(raw[3])
            elif version == 1:
                # creation_time(u64) modification_time(u64) timescale(u32) duration(u64)
                raw = struct.unpack_from(">QQIQ", data, fields_start)
                timescale, duration = int(raw[2]), int(raw[3])
            else:
                raise ValueError(f"unknown mvhd version {version}")

            if timescale == 0:
                return 0.0
            return float(duration) / timescale

        pos += box_size

    raise ValueError("mvhd box not found")


def _looks_like_cold_start(resp: httpx.Response) -> bool:
    if resp.status_code != 503:
        return False
    return "estimated_time" in resp.text[:512]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class HFVideoProvider(VideoProvider):
    """HF Inference API T2V provider using LTX-Video (free tier).

    Parameters
    ----------
    token:
        HuggingFace API token (``HF_TOKEN`` env var in production).
    client:
        Optional pre-built :class:`httpx.AsyncClient`. If ``None`` a new
        client is created with ``generate_timeout_s`` as the default timeout.
        The caller is responsible for closing an injected client; this class
        only closes the one it creates.
    generate_timeout_s:
        Total request timeout for ``generate()``. LTX-Video can take up to
        ~60 s on a warm instance; the default of 300 s is intentionally
        generous to survive cold starts without timing out on the first retry.
    """

    name = "hf"

    def __init__(
        self,
        *,
        token: str,
        client: httpx.AsyncClient | None = None,
        generate_timeout_s: float = _DEFAULT_GENERATE_TIMEOUT_S,
    ) -> None:
        if not token:
            raise ValueError("HFVideoProvider requires a non-empty token")
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=generate_timeout_s)
        self._owns_client = client is None
        self._generate_timeout_s = generate_timeout_s

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "video/mp4",
        }

    def _body(self, req: VideoRequest) -> dict[str, Any]:
        return {
            "inputs": req.prompt,
            "parameters": {
                "seed": req.seed,
                "num_frames": _derive_num_frames(req.duration_s, req.fps),
                "width": req.width,
                "height": req.height,
                "num_inference_steps": 50,
                "guidance_scale": 3.0,
            },
        }

    # ------------------------------------------------------------------
    # VideoProvider ABC
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """GET the HF API root; True when status < 500.

        Probes the base URL (not the model endpoint) so a model cold-start
        503 does not incorrectly mark the provider as unhealthy.
        Network errors return False without raising.
        """
        try:
            resp = await self._client.get(_BASE_URL, timeout=_HEALTH_TIMEOUT_S)
        except (httpx.HTTPError, OSError):
            return False
        return resp.status_code < 500

    async def generate(self, req: VideoRequest) -> VideoResult:
        t0 = time.perf_counter()

        try:
            resp = await self._client.post(
                _ENDPOINT,
                json=self._body(req),
                headers=self._headers(),
                timeout=self._generate_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise VideoProviderUnavailable(
                f"hf timeout after {self._generate_timeout_s}s"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise VideoProviderUnavailable(f"hf transport error: {exc!r}") from exc

        latency_ms = int((time.perf_counter() - t0) * 1000)

        if resp.status_code == 429:
            raise VideoProviderRateLimited(f"hf 429: {resp.text[:120]!r}")

        if _looks_like_cold_start(resp):
            raise VideoProviderUnavailable(
                f"hf cold start (503): {resp.text[:160]!r}"
            )

        if 500 <= resp.status_code < 600:
            raise VideoProviderUnavailable(
                f"hf {resp.status_code}: {resp.text[:120]!r}"
            )

        if resp.status_code in (401, 403):
            raise VideoProviderError(
                f"hf auth {resp.status_code}: {resp.text[:120]!r}"
            )

        if resp.status_code >= 400:
            raise VideoProviderError(
                f"hf {resp.status_code}: {resp.text[:120]!r}"
            )

        data = resp.content
        if not data:
            raise VideoProviderInvalidOutput("hf returned empty body")

        ct = resp.headers.get("content-type", "")
        if ct and "video" not in ct.lower():
            raise VideoProviderInvalidOutput(
                f"hf non-video content-type: {ct!r}"
            )

        actual_duration = _parse_mp4_duration(data)
        min_duration = req.duration_s * 0.8
        if actual_duration < min_duration:
            raise VideoProviderInvalidOutput(
                f"hf clip too short: {actual_duration:.2f}s "
                f"< {min_duration:.2f}s (80% of {req.duration_s}s)"
            )

        return VideoResult(
            bytes_=data,
            mime_type="video/mp4",
            provider=self.name,
            model=_MODEL,
            duration_s=actual_duration,
            frames_count=_derive_num_frames(req.duration_s, req.fps),
            latency_ms=latency_ms,
            cost_usd=0.0,
        )

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "max_duration_s": 10.0,
            "supported_resolutions": [(512, 512), (768, 512), (512, 768)],
            "supported_fps": [24],
        }
