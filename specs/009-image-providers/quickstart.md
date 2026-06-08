# Quickstart: ImageProvider Abstraction

**Branch**: `009-image-providers` | **Date**: 2026-06-07
**Depends on**: module 001 merged. No DB needed (this is pure code).

---

## 1. Set credentials

`.env.local`:

```ini
HUGGINGFACE_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx     # https://huggingface.co/settings/tokens
T2I_TIMEOUT_S=120
T2I_MAX_RETRIES=3
T2I_BACKOFF_SECONDS_CSV=2,6,18
# Pollinations is unauth — no key needed.
```

Restart the API to pick them up.

---

## 2. Quick smoke against Pollinations (live)

```sh
uv run python -c "
import asyncio
from app.providers.image import chain_for_env
from app.providers.image.base import ImageRequest

async def main():
    chain = chain_for_env('mvp')
    router = __import__('app.providers.image.router',
        fromlist=['ImageProviderRouter']).ImageProviderRouter(chain)
    req = ImageRequest(
        prompt='a quiet street in Buenos Aires, 1998, cinematic 35mm film',
        seed=42, width=1024, height=1024,
    )
    result = await router.render(req)
    print('provider=', result.provider, 'bytes=', len(result.bytes_),
          'mime=', result.mime_type, 'latency_ms=', result.latency_ms)
    # Save for inspection
    with open('/tmp/smoke.webp', 'wb') as f:
        f.write(result.bytes_)
    print('wrote /tmp/smoke.webp')

asyncio.run(main())
"
```

Expected: a webp file in `/tmp/`, viewable in any browser. provider=`pollinations`.

---

## 3. Quick smoke against HuggingFace

Force a Pollinations failure to exercise the fallback:

```sh
# Block Pollinations DNS at the OS level (mac/linux):
sudo bash -c 'echo "127.0.0.1 image.pollinations.ai" >> /etc/hosts'

# Re-run the previous script. provider=hf.
```

(Undo: edit `/etc/hosts` to remove the line.)

Alternatively, use the FakeImageProvider chain (no network):

```sh
uv run python -c "
import asyncio
from app.providers.image.base import ImageRequest, ImageResult
from app.providers.image.fake import FakeImageProvider, PNG_1x1
from app.providers.image.router import ImageProviderRouter

async def main():
    fake = FakeImageProvider(responses=[
        ImageResult(bytes_=PNG_1x1, mime_type='image/png',
                    provider='fake', model='fake:1x1', latency_ms=1),
    ])
    router = ImageProviderRouter([fake])
    result = await router.render(ImageRequest(prompt='x', seed=1))
    print(result.provider, len(result.bytes_), result.mime_type)

asyncio.run(main())
"
# fake 95 image/png
```

---

## 4. Test the router failover policy

```sh
uv run pytest tests/unit/test_image_router.py -v
```

Watch for the four named cases:
- `test_success_on_first_provider`
- `test_rate_limited_skips_to_next`
- `test_unavailable_retries_then_succeeds`
- `test_unavailable_all_providers_exhausted`
- `test_invalid_output_skips_no_retry`
- `test_health_false_skips_no_attempt`

---

## 5. Test the path helper

```sh
uv run pytest tests/unit/test_image_paths.py -v
```

Manual check:

```sh
uv run python -c "
from app.providers.image.paths import compute_r2_path
from app.providers.image.base import ImageResult
r = ImageResult(bytes_=b'fakebytes', mime_type='image/webp',
                provider='fake', model='x', latency_ms=1)
print(compute_r2_path('s01-el-tunel',
                      '9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718',
                      2, r))
"
# seasons/s01-el-tunel/9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718/2-aacb6a4a.webp
```

---

## 6. Verify the import-graph guard

```sh
uv run pytest tests/unit/test_image_import_graph.py -v
```

This test scans `app/api/`, `app/domain/`, `app/scripts/` and asserts no file
contains the banned literals (`image.pollinations.ai`,
`api-inference.huggingface.co`) or imports from
`app.providers.image.{pollinations,huggingface}` directly.

If a future module 008 PR tries to sneak a direct call, this test fails the PR.

---

## 7. Live tests (nightly)

```sh
uv run pytest -m live tests/live/test_pollinations_smoke.py -v
uv run pytest -m live tests/live/test_huggingface_smoke.py -v
```

These hit the real providers and are skipped by default in CI. The nightly
`live-llm-smoke.yml` workflow (from module 006) is extended to run them.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImageProviderInvalidOutput: content-type text/html` from Pollinations | Their CDN returned an error page disguised as 200 | Treat as expected; router skips to HF |
| `ImageProviderUnavailable: model is currently loading` from HF | First HF call to a cold model | Backoff handles it; subsequent calls warm. If persists > 5 min, model retired |
| Test failure: "banned literal `image.pollinations.ai` found" | Someone wrote a direct httpx call in a business module | Move the call behind `ImageProvider` |
| `KeyError` in `compute_r2_path` | mime_type not in the allowlist | Provider produced an unsupported format; treat as `InvalidOutput` |
| `chain_for_env("v02")` raises NotImplementedError | LocalComfyProvider not yet shipped | Expected until v0.2 |
