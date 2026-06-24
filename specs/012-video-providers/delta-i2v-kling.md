# Delta v2 — I2V Kling (Ronda 7 pivot)

**Applies to**: `specs/012-video-providers/` | **Date**: 2026-06-24
**Triggered by**: SDD Ronda 7 (decisions #28-#34, ADR-0008); pivot from T2V free
to I2V Kling paid as primary, with T2V kept as fallback.
**Read alongside**: original `spec.md`, `research.md`, `tasks.md` in this folder,
`specs/013-characters-catalog/` (provides the `image_url` seed), and the original
`specs/008-generation-pipeline/delta-video.md` (which this delta interlocks with).

---

## 1. What changes, what stays, what is new

### Stays untouched
- T-001 (`VideoProvider` ABC + `VideoRequest` + `VideoResult` + 3 exceptions)
  — kept verbatim; T2V is still a viable fallback layer.
- T-002 (`FakeVideoProvider`) — unchanged.
- T-003 (`HFVideoProvider`) — unchanged; becomes fallback layer 1.
- T-004 (`PollinationsVideoProvider`) — unchanged; becomes fallback layer 2.
- T-005 (`VideoProviderRouter` T2V) — unchanged; consumed by the new I2V
  router as a downstream fallback chain.
- T-006 (`compute_r2_clip_path`) — unchanged; the path contract is reused
  for I2V output (the `provider` segment expands to include `"kling"`).
- T-009 (live smoke for T2V) — unchanged.
- T-010 (import-graph guard) — extended to also cover the new I2V
  subpackage; same enforcement pattern.

### Changes (existing tasks modified)
- **T-007** — `chain_for_env(env, …)` gains a second return slot for the
  **I2V chain**. The factory now returns
  `(image_to_video_router, video_router)` so module 008's coordinator can
  pick which one to invoke. New env values:
  - `dev` → `(FakeImageToVideoProvider, FakeVideoProvider)` — fully
    sandboxed, no paid call.
  - `mvp` → `(KlingI2VProvider, VideoProviderRouter([HFVideoProvider,
    PollinationsVideoProvider]))` — primary paid + degraded free.
  - `live_t2v_only` (new) → `(None, VideoProviderRouter([…]))` — disables
    I2V entirely; used by ops if Kling outage exceeds the budget killswitch.
- **T-008** — paid stubs (`KlingProvider`, `RunwayProvider`, `LumaProvider`)
  partially superseded. The `KlingProvider` **T2V** stub remains (Kling
  has a T2V endpoint too, not used by us), but the production codepath
  no longer references it. Runway/Luma stubs unchanged.

### New (added tasks)

- **T-011** — New ABC `ImageToVideoProvider` in
  `app/providers/image_to_video/base.py` with `ImageToVideoRequest`,
  `ImageToVideoResult`, and the same four typed exceptions
  (`ImageToVideoProviderError` + `RateLimited` / `Unavailable` /
  `InvalidOutput`) — parallel to the T2V hierarchy. See §3 FR-NEW-1.
- **T-012** — `KlingI2VProvider` real impl in
  `app/providers/image_to_video/kling.py`. POST to Kling AI v1 image-to-video
  endpoint; auth via bearer `KLING_API_KEY`; polling for async job
  completion; downloads result `.mp4` bytes; duration validation via
  `mutagen` (same dep added in original T-001). See §3 FR-NEW-2.
- **T-013** — `FakeImageToVideoProvider` mirror of `FakeVideoProvider` for tests.
- **T-014** — Budget tracking table + repo:
  - Migration `0009_kling_usage_month.py` (see §4).
  - `KlingUsageRepo` with two methods:
    `current_month_credits_used() -> int` and
    `record_credits(amount: int) -> int`.
- **T-015** — `ImageToVideoProviderRouter` in
  `app/providers/image_to_video/router.py`:
  - Single-entry chain (only `KlingI2VProvider` in MVP).
  - **Budget pre-check**: before calling `provider.generate()`, query
    `KlingUsageRepo.current_month_credits_used()`. If
    `remaining_pct < KLING_BUDGET_KILLSWITCH_PCT` (default 20),
    immediately raise `ImageToVideoProviderUnavailable("budget_killswitch")`
    so the coordinator falls back to T2V without spending the last credits.
  - On `generate()` success: call
    `KlingUsageRepo.record_credits(result.credits_used)`.
  - Same exception-driven policy as `VideoProviderRouter` for the rest.
- **T-016** — Live smoke test for Kling (`@pytest.mark.live`). Reads
  `KLING_API_KEY` from `.env.local`; gated by env presence; nightly only.
- **T-017** — Import-graph guard extended: module 008 imports only the
  facades (`app.providers.image_to_video` and `app.providers.video`),
  not the per-provider files.

---

## 2. New dependencies

- No new Python packages. `httpx`, `mutagen`, `structlog` already in
  the project from original T-001 and module 009.
- New env vars in `settings.py` (loaded from `.env.local` / Fly secrets):

| Var | Type | Default | Notes |
|---|---|---|---|
| `KLING_API_KEY` | `str \| None` | `None` | Bearer token. None in dev. |
| `KLING_API_BASE_URL` | `str` | `https://api.kling.ai/v1` | Override for staging. |
| `KLING_PLAN_TIER` | `Literal["standard","premium"]` | `"standard"` | Picks the model id (`kling-v1-std` vs `kling-v1-pro`). |
| `KLING_BUDGET_CREDITS_MAX` | `int` | `30` | Monthly hard cap. Plan Standard ≈ 30 generations/mo. |
| `KLING_BUDGET_KILLSWITCH_PCT` | `int` | `20` | Percentage of remaining credits below which the router refuses. |
| `KLING_TIMEOUT_S` | `int` | `600` | I2V is slower than T2V; 10 min is realistic. |
| `KLING_POLL_INTERVAL_S` | `int` | `5` | Kling jobs return a task_id; we poll. |
| `KLING_CLIP_DURATION_S` | `int` | `10` | Locked by SDD Ronda 7 #30. Validated at `ImageToVideoRequest` build. |

All TBDs (exact tier name, exact credits/mo, exact endpoint shape) are
resolved by **R-NEW-1** in this delta (see §6) before implementation begins.

---

## 3. Changed and new Functional Requirements

### FR-NEW-1 — `ImageToVideoProvider` ABC

```python
# app/providers/image_to_video/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ImageToVideoRequest:
    image_url: str                                # public R2 URL of the seed photo
    prompt: str                                   # motion / scene description
    duration_s: int = 10                          # locked to KLING_CLIP_DURATION_S
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    seed: int | None = None                       # provider-supported best-effort


@dataclass(frozen=True)
class ImageToVideoResult:
    bytes_: bytes
    mime_type: Literal["video/mp4"]
    provider: str                                 # "kling" | "fake"
    model: str                                    # e.g., "kling-v1-std"
    duration_s: float                             # parsed from mp4 metadata
    frames_count: int
    latency_ms: int
    credits_used: int                             # NEW — drives budget repo
    cost_usd: float                               # informational only


class ImageToVideoProviderError(Exception): ...
class ImageToVideoProviderRateLimited(ImageToVideoProviderError): ...
class ImageToVideoProviderUnavailable(ImageToVideoProviderError): ...
class ImageToVideoProviderInvalidOutput(ImageToVideoProviderError): ...


class ImageToVideoProvider(ABC):
    @abstractmethod
    async def health(self) -> bool: ...
    @abstractmethod
    async def generate(self, req: ImageToVideoRequest) -> ImageToVideoResult: ...
```

**Why a separate ABC** (not a method on `VideoProvider`): the input shape
differs (`image_url` is mandatory in I2V, absent in T2V) and the result
carries `credits_used` which has no T2V analogue. Forcing them into one
ABC would either widen `VideoRequest` with optional fields (Gate 4
violation per the spec.md of 012 — "request signature is frozen") or
push the dispatching into a string discriminator. A parallel hierarchy
keeps both type-clean.

### FR-NEW-2 — `KlingI2VProvider` implementation

`generate()` body:

1. Validate `req.duration_s == settings.kling_clip_duration_s`; else raise
   `InvalidOutput("duration_mismatch")` (defensive — the orchestrator
   should already pin this).
2. POST to `KLING_API_BASE_URL/image-to-video` with JSON body
   `{model_name, image_url, prompt, duration, aspect_ratio, cfg_scale}`.
3. Receive `{task_id, status}`. Poll `GET /tasks/{task_id}` every
   `KLING_POLL_INTERVAL_S` until `status in {"succeed","failed"}` or
   total elapsed > `KLING_TIMEOUT_S`.
4. On succeed: download video from `result.video_url`. Validate duration
   via `mutagen` (≥ 80% of requested). Parse `credits_used` from job
   metadata.
5. Status translations:
   - HTTP 401/403 → `Unavailable("auth")` (alerts ops; not a rate-limit).
   - HTTP 429 → `RateLimited`.
   - HTTP 5xx / timeout / poll exhaustion → `Unavailable`.
   - Job status `"failed"` → `InvalidOutput` (the model returned an
     unusable clip; not retried).
   - Duration < 80% → `InvalidOutput`.

### FR-NEW-3 — Budget killswitch

Inside `ImageToVideoProviderRouter.render(req)`, before invoking the
chain:

```python
async with kling_usage_repo as repo:
    used = await repo.current_month_credits_used()
    remaining_pct = max(0, 100 * (settings.kling_budget_credits_max - used)
                              // settings.kling_budget_credits_max)
    if remaining_pct < settings.kling_budget_killswitch_pct:
        log.warning("i2v_budget_killswitch", used=used,
                    max=settings.kling_budget_credits_max,
                    remaining_pct=remaining_pct)
        raise ImageToVideoProviderUnavailable("budget_killswitch")
```

The coordinator (module 008 delta) catches this specific exception path
and proceeds to the T2V router fallback chain.

### FR-NEW-4 — Credits recording is idempotent per chapter

```python
await kling_usage_repo.record_credits(
    amount=result.credits_used,
    chapter_id=chapter_id,            # idempotency key
)
```

If the coordinator retries the same chapter (e.g., generation rerun via
`POST /internal/generation/rerun`), the repo writes only **once** per
`(year_month, chapter_id)`. See §4 schema.

---

## 4. Data model delta

### New table — `kling_usage_month`

```sql
CREATE TABLE kling_usage_month (
    year_month   CHAR(7)     NOT NULL,            -- 'YYYY-MM'
    chapter_id   BIGINT      NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    credits_used INTEGER     NOT NULL CHECK (credits_used >= 0),
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (year_month, chapter_id)
);

CREATE INDEX idx_kling_usage_year_month
    ON kling_usage_month (year_month);
```

| Column | Type | Notes |
|---|---|---|
| `year_month` | CHAR(7) | `strftime('%Y-%m', ART now())`. ART-anchored — Gate 3. |
| `chapter_id` | BIGINT | Idempotency key per chapter. Cascading delete keeps the table clean if a chapter is rolled back. |
| `credits_used` | INTEGER | Per-job cost. Always ≥ 0. |
| `recorded_at` | TIMESTAMPTZ | Audit. |

`current_month_credits_used()` query:

```sql
SELECT COALESCE(SUM(credits_used), 0)
FROM kling_usage_month
WHERE year_month = $1;       -- parameterised current ART year_month
```

`record_credits()` (idempotent UPSERT):

```sql
INSERT INTO kling_usage_month (year_month, chapter_id, credits_used)
VALUES ($1, $2, $3)
ON CONFLICT (year_month, chapter_id) DO NOTHING;
```

`DO NOTHING` is intentional: the **first successful** generation for a
chapter is the source of truth. Re-runs are charged to the same
chapter row and idempotently no-op the second insert. This matches the
"all-or-nothing per chapter" cost model.

### Migration `0009_kling_usage_month.py` (sketch)

```python
def upgrade() -> None:
    op.create_table(
        'kling_usage_month',
        sa.Column('year_month', sa.CHAR(7), nullable=False),
        sa.Column('chapter_id', sa.BigInteger(),
                  sa.ForeignKey('chapters.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('credits_used', sa.Integer(),
                  sa.CheckConstraint('credits_used >= 0'),
                  nullable=False),
        sa.Column('recorded_at', sa.TIMESTAMPTZ,
                  server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('year_month', 'chapter_id'),
    )
    op.create_index('idx_kling_usage_year_month',
                    'kling_usage_month', ['year_month'])


def downgrade() -> None:
    op.drop_index('idx_kling_usage_year_month', table_name='kling_usage_month')
    op.drop_table('kling_usage_month')
```

---

## 5. Changed FRs from the original spec

### FR-005 delta — Router output

The original FR-005 said the router emits `video_provider_attempt …` logs
only. Extended:

- `i2v_provider_attempt {provider, attempt, outcome, latency_ms, credits_used}`
  on each call into the I2V router.
- `i2v_budget_killswitch {used, max, remaining_pct}` when budget is below
  threshold.
- `i2v_to_t2v_failover {reason}` when the I2V router raises and the
  coordinator (module 008) switches to T2V.

### FR-007 — Settings (extended)

In addition to the original `T2V_*` settings, add all eight `KLING_*`
vars from §2. `chain_for_env("mvp", settings)` must:

- Refuse to construct a `KlingI2VProvider` if `KLING_API_KEY is None`.
  Logs `i2v_chain_disabled_no_key` and returns `(None, t2v_router)`.
- Refuse if `KLING_BUDGET_CREDITS_MAX <= 0`. Same behavior.

This means an incomplete prod config never silently makes paid calls;
it gracefully degrades to T2V (and the existing T2I fallback below
that).

---

## 6. Phase 0 Research

### R-NEW-1 — Kling API surface

**Question**: which exact Kling AI plan, model id, endpoint shape, and
credit-cost-per-generation does this module target?

**Action items** (resolved **before** T-012 implementation):

- Subscribe to **Kling AI Standard** (or whichever is the cheapest tier
  that exposes the API at writing time). Tentative: ~USD 4.66/mo billed
  annually.
- Confirm the API base URL (the public docs URL has changed in 2026; the
  reference URL is `KLING_API_BASE_URL` and is settable per env).
- Confirm: 1 generation of 10 s = 1 credit on Standard, ≈ 30 credits/mo.
  If different, set `KLING_BUDGET_CREDITS_MAX` to the true value.
- Capture the JSON request/response shapes in this file before T-012
  starts.

### R-NEW-2 — Image rights amplified

The I2V seed image comes from the public R2 catalog (module 013). The
rights posture from module 013 R-003 carries over. **Output** (the
generated 10 s clip) is a derivative work; its distribution to the
closed-beta cohort is the same risk profile as the cómic output (T2I
also produced derivative likenesses).

No additional action here — module 013's takedown plan covers both seeds
and outputs.

### R-NEW-3 — Polling vs webhooks

Kling supports webhook callbacks for long jobs. For MVP we **poll**
(simpler ops, no public ingress required). Webhook upgrade is reserved
for v0.2 if generation latency on Premium tier exceeds the GENERACION
window.

---

## 7. Tests delta

- **Unit** — `KlingI2VProvider`:
  - 401/403 → `Unavailable("auth")`.
  - 429 → `RateLimited`.
  - Poll exhaustion (`KLING_TIMEOUT_S` elapsed) → `Unavailable("timeout")`.
  - `status=failed` from poll → `InvalidOutput`.
  - Duration < 80% → `InvalidOutput`.
  - Happy path returns a `ImageToVideoResult` with `credits_used > 0`.
- **Unit** — `KlingUsageRepo`:
  - `record_credits` upsert is idempotent on `(year_month, chapter_id)`.
  - `current_month_credits_used` sums correctly across rows.
- **Unit** — `ImageToVideoProviderRouter`:
  - Budget killswitch fires when `remaining_pct < killswitch_pct`.
  - Budget killswitch does **not** fire at exactly the threshold.
  - On `generate()` success, `record_credits` is called once.
  - Logs the 3 new events from FR-005 delta.
- **Integration** — `chain_for_env('mvp', ...)`:
  - With `KLING_API_KEY=None` → returns `(None, t2v_router)` + warn log.
  - With key → returns `(I2VRouter, t2v_router)`.
- **Live** (`@pytest.mark.live`) — T-016: real Kling call, deduct one
  credit, asserts duration ≈ 10 s. Skipped in CI; nightly only.

---

## 8. Acceptance for "delta done"

- [ ] `app/providers/image_to_video/` package exists with `base.py`,
      `kling.py`, `fake.py`, `router.py`. All `mypy --strict` clean.
- [ ] Migration `0009_kling_usage_month.py` applies + rolls back clean.
- [ ] `KlingI2VProvider` unit tests cover all 6 error paths from §7.
- [ ] `KlingUsageRepo.record_credits` idempotent on
      `(year_month, chapter_id)` — integration test.
- [ ] `ImageToVideoProviderRouter` budget killswitch unit-tested.
- [ ] `chain_for_env` returns `(None, t2v_router)` when key missing —
      integration test.
- [ ] Import-graph guard updated to cover the new subpackage.
- [ ] R-NEW-1 (Kling plan + credits + endpoint shape) verified and
      captured back into this doc before T-012 implementation.
- [ ] Module 008 delta can import `ImageToVideoProvider` and the router
      without further changes here.
