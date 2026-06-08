# Phase 0 Research: ImageProvider Abstraction

**Branch**: `009-image-providers` | **Date**: 2026-06-07

---

## R-001 — HTTP client: raw `httpx` vs vendor SDKs

**Question**: do we use `huggingface-hub` and (hypothetical) Pollinations SDK,
or raw `httpx` calls?

| Option | Pros | Cons |
|---|---|---|
| **Raw `httpx` (chosen)** | Smallest dep surface; transparent behavior; identical pattern across providers | Manual auth header handling; we own retry/timeout |
| `huggingface-hub` SDK | Idiomatic HF API access | Pulls in transitive deps (filelock, fsspec, etc.); locks us to HF's release cadence |
| Pollinations SDK | Doesn't exist (Pollinations is unauth HTTP) | N/A |

**Decision**: **raw `httpx`**. Both providers are HTTP-based with simple semantics
(GET for Pollinations, POST for HF). The cost of writing the auth + JSON
serialization is small (~30 LOC each) and saves transitive dependency surface
that violates the "zero-cost" spirit (smaller deploys, fewer security advisories
to track).

**Streaming**: `httpx.AsyncClient.stream("GET", url)` is used for Pollinations so
that a multi-MB response doesn't blow up memory on the Fly free tier (256 MB
machine). HF returns binary blobs directly; `response.content` is fine.

---

## R-002 — Exception taxonomy

**Question**: which exceptions does `generate()` raise and what does each mean
for retry semantics?

**Decision** (mirrors SDD §4.5.1):

| Exception | Trigger | Router behavior |
|---|---|---|
| `ImageProviderRateLimited` | HTTP 429, "Rate limit exceeded" body | **Skip** to next provider immediately (no retries here) |
| `ImageProviderUnavailable` | HTTP 5xx, network timeout, model cold-start | **Retry** with exponential backoff (FR-005) |
| `ImageProviderInvalidOutput` | Non-image content type, 0 bytes, parse failure | **Skip** to next provider; **no retry** (provider bug, not transient) |
| `ImageProviderError` (base) | Generic failure | Should not be raised; only subclasses |

**Rationale**: this taxonomy decouples policy (router) from mechanism (provider).
Each provider raises the most specific exception it can detect; the router
applies the policy uniformly.

---

## R-003 — Backoff parameters and total bound

**Question**: how aggressive is the retry?

**Decision**: `T2I_BACKOFF_SECONDS = [2, 6, 18]`. Three retries per provider, on
`Unavailable` only. Total wait per provider in the worst case:
`2 + 6 + 18 = 26 s` of sleep + 3× HTTP timeout (default 120 s) = ~6 min per
provider. With 2 providers in chain, up to ~12 min per `render()` call.

**Why these numbers**: Pollinations and HF Inference cold-starts can take 30–60
s; 2 s and 6 s give the service time to recover transient blips, while 18 s
covers genuine cold-starts.

**Module 008 hard deadline**: `PIPELINE_HARD_DEADLINE_S = 3300 s` (55 min, from
SDD §3.2). With 3 panels × 12 min worst case = 36 min, we have headroom.

**Configurable via env**: `T2I_BACKOFF_SECONDS_CSV="2,6,18"` for tuning.

---

## R-004 — `compute_r2_path` location

**Question**: does the path-derivation helper live with the providers, with the
generation pipeline (008), or with the storage layer?

**Decision**: **with the providers** (`paths.py` in `app/providers/image/`).

**Rationale**: the path scheme embeds the **content hash of the image bytes**,
which is the provider's output. Putting the helper next to the producer keeps
the dependency direction clean: 008 imports from 009. Putting it in 008 would
create a leaky abstraction (008 reaching down into how providers represent
content).

The path scheme:
```
seasons/{season_slug}/{chapter_public_id}/{panel_idx}-{sha256(bytes_)[:8]}.{ext}
```

- Hash-prefix prevents enumeration of future-chapter assets (R2 is public).
- `panel_idx` makes the path human-readable for ops.
- Same input bytes → same path → R2 PUT is idempotent.

---

## R-005 — `LocalComfyProvider` reservation strategy

**Question**: how do we "reserve" v0.2 without writing the code?

**Decision**: ship a placeholder file `local_comfy.py` with:

```python
class LocalComfyProvider(ImageProvider):
    name = "local_comfy"
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "LocalComfyProvider arrives in v0.2 (SDD §4.5.2 / OQ-3). "
            "See docs/adr/0003-image-provider-v02.md for the integration plan."
        )
```

And in `chain_for_env`:

```python
def chain_for_env(env):
    if env == "v02":
        # Will be enabled when v0.2 ships.
        raise NotImplementedError("v02 chain not available yet")
    ...
```

**Why a stub class vs nothing**: documents intent in the codebase; future
contributors find a hook with a clear error message; the import surface stays
consistent for tooling.

---

## R-006 — Watermark / NSFW filtering (out of scope, but documented)

**Question**: should the router validate that returned images don't contain
watermarks or NSFW content?

**Decision**: **not in MVP**. SDD §8 R-9 lists this as a known risk.

**Reasoning**: real watermark detection requires CLIP-based embeddings or
similar ML, which would either:
- Pull in `transformers` + `torch` (huge dep, memory hog), or
- Add another paid API.

Both violate Gate 1. For closed beta, the cost of an occasional watermark
slipping through is low. Module 008 marks the chapter `ready_degraded` if any
panel fails; an unwanted watermark doesn't trigger this, but a manual `pnpm
rerun-filter`–style mechanism could be added in a future module.

**Trigger to revisit**: a season with > 5 % watermarked images surfaces in the
beta. Then add a CLIP-based check behind a feature flag.

---

## R-007 — Concurrent renders against the same router

**Question**: module 008 may call `router.render` for panels 1, 2, 3 concurrently
to save wall-clock time. Does the router support this?

**Decision**: **yes, trivially**. The router is stateless; each `render` call
creates its own attempt cursor and provider call sequence. The underlying
`httpx.AsyncClient` is thread/coroutine-safe.

**Caveat**: if Pollinations rate-limits, all three concurrent calls hit the
limit simultaneously and all three fall over to HF. That's a self-DoS pattern
but bounded by the chain length. Acceptable for MVP (3 panels max).

**Trigger to revisit**: when "chapter has many more panels" becomes a thing —
serialize against a per-provider semaphore.

---

## R-008 — Testing strategy: `FakeImageProvider` invariants

**Question**: what does `FakeImageProvider` need to do well to be useful?

**Decision**: minimum surface:

```python
class FakeImageProvider(ImageProvider):
    name = "fake"
    def __init__(self,
                 responses: list[ImageResult | type[Exception] | Exception],
                 latency_ms: int = 0,
                 health_returns: bool = True):
        ...

    async def health(self) -> bool: return self.health_returns
    async def generate(self, req) -> ImageResult:
        if self.latency_ms: await asyncio.sleep(self.latency_ms / 1000)
        item = self._pop_response()
        if isinstance(item, type) and issubclass(item, Exception): raise item()
        if isinstance(item, Exception): raise item
        return item
```

**Default `ImageResult`** for tests: 1×1 transparent PNG bytes (`PNG_1x1`
constant in `fake.py`).

Tests in module 008 will pre-seed `responses` with the exact sequence they want
to assert against.

---

## R-009 — Auth on HF

**Question**: HF Inference API requires a bearer token. How do we get one for
free?

**Decision**: each developer / operator creates a free HF account, generates a
read-only token at `huggingface.co/settings/tokens`, and sets
`HUGGINGFACE_TOKEN` in `.env.local` / Fly secrets. Tokens are personal; not
committed; rotated like any other secret.

For CI: a single org-level token in repo secrets (not used in PR CI; only in
`live-llm-smoke.yml` nightly).

---

## Open items

- **OQ-IP-1**: per-provider semaphore for concurrent calls (R-007 trigger).
- **OQ-IP-2**: watermark detection (R-006 trigger).
- **OQ-IP-3**: cost tracking when a future paid provider is added — `cost_usd`
  is already in `ImageResult` for forward compatibility.
