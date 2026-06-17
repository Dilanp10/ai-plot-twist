# Quickstart: VideoProvider Abstraction

**Branch**: `012-video-providers` | **Date**: 2026-06-16
**Depends on**: module 001 merged. No DB needed (this is pure code).

---

## 1. Install the new dependency

```sh
cd apps/api
uv add "mutagen~=1.47"
```

Verify:

```sh
uv run python -c "from mutagen.mp4 import MP4; print('mutagen ok')"
```

---

## 2. Set credentials

`.env.local`:

```ini
HUGGINGFACE_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx     # https://huggingface.co/settings/tokens
T2V_TIMEOUT_S=300
T2V_MAX_RETRIES=3
T2V_BACKOFF_SECONDS_CSV=5,15,45
# Pollinations video is unauth — no key needed.
```

Same HF token used for module 009 (`HUGGINGFACE_TOKEN`) works here — no
separate token required. Restart the API to pick up env changes.

---

## 3. Quick smoke against HF LTX-Video (live)

```sh
uv run python -c "
import asyncio
from app.providers.video import chain_for_env
from app.providers.video.base import VideoRequest
from app.providers.video.router import VideoProviderRouter

async def main():
    chain = chain_for_env('mvp')
    router = VideoProviderRouter(chain)
    req = VideoRequest(
        prompt='a quiet street in Buenos Aires, 1998, cinematic 35mm film, moody',
        seed=42,
        duration_s=5.0,
        width=512,
        height=512,
        fps=24,
        aspect='9:16',
    )
    result = await router.render(req)
    print('provider=', result.provider, 'bytes=', len(result.bytes_),
          'mime=', result.mime_type, 'duration_s=', result.duration_s,
          'latency_ms=', result.latency_ms)
    with open('/tmp/smoke_clip.mp4', 'wb') as f:
        f.write(result.bytes_)
    print('wrote /tmp/smoke_clip.mp4')

asyncio.run(main())
"
```

Expected output: `provider= hf`, an `.mp4` written to `/tmp/smoke_clip.mp4`.
Open it in any media player to verify. First call can take 120-300 s (cold
model start — see Troubleshooting below).

---

## 4. Quick smoke against Pollinations video (live)

Force HF failure to exercise the fallback chain:

```sh
# Block HF DNS (mac/linux):
sudo bash -c 'echo "127.0.0.1 api-inference.huggingface.co" >> /etc/hosts'

# Re-run the previous script. Should produce provider= pollinations.
# Undo afterwards:
# sudo sed -i '' '/api-inference.huggingface.co/d' /etc/hosts
```

> **Note**: if Pollinations video beta is down or has changed its endpoint,
> `PollinationsVideoProvider.health()` returns `False`, the router logs
> `video_provider_skipped`, and the chain exhausts — raising
> `VideoProviderUnavailable`. This is expected behaviour; module 008 catches it
> and falls back to T2I. See Troubleshooting §7.

---

## 5. Smoke with FakeVideoProvider (no network)

```sh
uv run python -c "
import asyncio
from app.providers.video.base import VideoRequest, VideoResult
from app.providers.video.fake import FakeVideoProvider, MINIMAL_MP4
from app.providers.video.router import VideoProviderRouter

async def main():
    fake = FakeVideoProvider(responses=[
        VideoResult(
            bytes_=MINIMAL_MP4,
            mime_type='video/mp4',
            provider='fake',
            model='fake',
            duration_s=5.0,
            frames_count=121,
            latency_ms=1,
        ),
    ])
    router = VideoProviderRouter([fake])
    result = await router.render(VideoRequest(prompt='x', seed=1))
    print(result.provider, len(result.bytes_), result.mime_type, result.duration_s)

asyncio.run(main())
# fake <N> video/mp4 5.0
"
```

---

## 6. Test _derive_num_frames edge cases

```sh
uv run pytest tests/unit/test_video_num_frames.py -v
```

Key cases to watch:
- `duration_s=5.0, fps=24` → `num_frames=121`
- `duration_s=4.0, fps=24` → `num_frames=97`
- `duration_s=2.0, fps=24` → `num_frames=49`
- `duration_s=5.1, fps=24` → `num_frames=121` (rounds to nearest valid)
- `duration_s=0.5, fps=24` → `num_frames=9` (minimum: n=1 → 9)

---

## 7. Test the router fallback policy

```sh
uv run pytest tests/unit/test_video_router.py -v
```

Watch for these cases:
- `test_success_on_first_provider`
- `test_rate_limited_skips_to_next`
- `test_unavailable_retries_then_succeeds`
- `test_unavailable_all_providers_exhausted`
- `test_invalid_output_skips_no_retry`
- `test_health_false_skips_no_attempt`
- `test_short_clip_within_tolerance_accepted`
- `test_short_clip_below_tolerance_invalid_output`
- `test_stub_provider_raises_not_implemented`

---

## 8. Test the clip path helper

```sh
uv run pytest tests/unit/test_video_paths.py -v
```

Manual check:

```sh
uv run python -c "
from app.providers.video.paths import compute_r2_clip_path
from app.providers.video.base import VideoResult
from app.providers.video.fake import MINIMAL_MP4

r = VideoResult(bytes_=MINIMAL_MP4, mime_type='video/mp4',
                provider='fake', model='fake', duration_s=5.0,
                frames_count=121, latency_ms=1)
print(compute_r2_clip_path('s01-el-tunel',
                           '9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718',
                           0, r))
# seasons/s01-el-tunel/9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718/clips/0-<hash>.mp4
"
```

---

## 9. Test paid stubs

```sh
uv run pytest tests/unit/test_video_stubs.py -v
```

Asserts that `KlingProvider`, `RunwayProvider`, `LumaProvider`:
- Are importable.
- Are subclasses of `VideoProvider`.
- Raise `NotImplementedError` on `health()` and `generate()`.
- Have `capabilities` populated without raising.

---

## 10. Verify the import-graph guard

```sh
uv run pytest tests/unit/test_video_import_graph.py -v
```

Scans `app/api/`, `app/domain/`, `app/scripts/` and asserts no file contains:
- `"video.pollinations.ai"`
- `"api-inference.huggingface.co/models/Lightricks"`
- Direct imports from `app.providers.video.hf`, `.pollinations`, `.kling`,
  `.runway`, or `.luma` outside `app/providers/video/`.

---

## 11. Live tests (nightly)

```sh
uv run pytest -m live tests/live/test_hf_video_smoke.py -v
uv run pytest -m live tests/live/test_pollinations_video_smoke.py -v
```

Skipped by default in CI. The nightly `live-llm-smoke.yml` workflow is extended
to run them. Each test asserts:
- `result.mime_type == "video/mp4"`
- `result.duration_s >= req.duration_s * 0.8`
- `len(result.bytes_) > 0`
- File written to `/tmp/` is playable (checked via `mutagen.mp4.MP4`).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| First HF call takes 120-300 s | Cold model start (LTX-Video) | Expected. Classified as `Unavailable` if it times out; backoff handles it. Second call is warm (~20-60 s). |
| `VideoProviderUnavailable: model is currently loading` from HF | 503 during cold start | Backoff `[5, 15, 45]` handles it. If it persists > 5 min, model may be retired — check HF model page. |
| `VideoProviderInvalidOutput: duration too short` | Provider returned a clip shorter than 80% of `req.duration_s` | Router skips to next provider. If all providers fail this way, reduce `req.duration_s`. |
| `VideoProviderInvalidOutput: content-type text/html` from Pollinations | CDN error page returned as 200 | Expected; router skips to HF. |
| `MutagenError` in logs | Provider returned a corrupt MP4 | Re-raised as `InvalidOutput`; router skips. If persistent, provider may have changed its output format. |
| `chain_for_env("paid_v1")` raises `NotImplementedError` | Paid stubs not yet implemented | Expected until a future paid-T2V module ships. Use `"mvp"` or `"dev"`. |
| `video_provider_skipped {provider:"pollinations", reason:"health_false"}` | Pollinations video beta endpoint changed or down | Expected. Router moves to HF. If HF also fails, module 008 degrades to T2I (module 009). Update `PollinationsVideoProvider` URL if the endpoint has moved. |
| Test failure: "banned literal found" | Direct `httpx` call to provider URL in a business module | Move the call behind `VideoProvider` abstraction. |
| `KeyError` in `compute_r2_clip_path` | Unexpected mime_type | Only `video/mp4` is accepted in MVP. Provider produced unsupported format; treat as `InvalidOutput` upstream. |
