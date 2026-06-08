# Phase 0 Research: Web Push

**Branch**: `011-web-push` | **Date**: 2026-06-07

---

## R-001 — `pywebpush` vs roll-our-own

**Question**: should we implement VAPID JWT signing + ECDH + AES-GCM
encryption ourselves, or use a library?

**Decision**: **`pywebpush`**.

**Rationale**: Web Push is RFC 8030 + RFC 8291 + RFC 8292. The encryption
math is non-trivial (P-256 ECDH, HKDF, AES-128-GCM). Rolling our own is a
clear-cut "do not roll your own crypto" violation. `pywebpush` is mature
(active since 2017), small (~1k LOC), Python-pure, and has a 2-line API:

```python
from pywebpush import webpush
webpush(
    subscription_info={"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}},
    data=json.dumps(payload),
    vapid_private_key=VAPID_PRIVATE_KEY,
    vapid_claims={"sub": VAPID_SUBJECT},
)
```

**Trigger to revisit**: never expected; if `pywebpush` is abandoned, the
RFC layer below it (`http_ece`) is stable and we could fork.

---

## R-002 — Hard delete on `Gone` (Gate 7 exception)

**Question**: Gate 7 mandates soft delete on user content. Should
`push_subscriptions` follow?

**Decision**: **hard delete**, with an explicit constitutional exception.

**Rationale**: `push_subscriptions` rows are **device credentials**, not user
content. The user produced nothing; we issued them a row when they granted
permission. On `Gone` (410), the credential is mathematically invalid — keeping
it as `deleted_at NOT NULL` provides no audit value and adds noise to queries.

This exception is narrow: it applies only to `push_subscriptions`. The
constitution will be amended via `docs/adr/0007-push-subscription-hard-
delete.md` to add the carve-out to Gate 7.

---

## R-003 — Fan-out concurrency model

**Question**: serial or parallel pushes?

**Decision**: **parallel via `asyncio.gather`** with:
- Per-call timeout: 10 s (via `asyncio.wait_for`).
- Total soft deadline: `PUSH_FANOUT_TIMEOUT_S = 60`.

At MVP scale (≤ 100 subs), all calls fire concurrently. `pywebpush` is sync;
we wrap each call in `loop.run_in_executor` (same pattern as `boto3` in
module 008 research R-007).

**Rationale**: serial would take 30+ s for 100 subs at typical push-server
latencies; parallel completes in ~3 s. The browser push services (FCM, autopush)
are battle-tested for parallel.

**Burst-protection**: not implemented in MVP. If FCM rate-limits us in beta,
add a `asyncio.Semaphore(20)` ceiling.

---

## R-004 — Idempotency on fan-out

**Question**: if the fan-out is re-invoked (admin replay, ESTRENO retry by
GH Actions), should it re-send?

**Decision**: **no, idempotent on `push_fanout:<chapter_uuid>`**.

**Why**: notifications are a user-trust surface. A duplicate ping at 12:00 ART
because GH Actions cron retried looks like a bug to users.

**Implementation**: at the start of `push_fanout`, INSERT into
`idempotency_keys` with key `push_fanout:<chapter.public_id>`. On UNIQUE
violation, log `push_fanout_skipped_idempotent` and return.

**Device-side dedup**: the notification `tag` (`chapter-<uuid>`) makes the
browser replace any prior notification with the same tag — so even without
server-side dedup, the user sees at most one. Defense in depth.

**Admin override**: the admin test endpoint accepts `?force=true` to bypass
idempotency (for testing).

---

## R-005 — Cleanup of stale subscriptions

**Question**: when do we delete subscriptions that aren't `Gone` but are flaky
(repeated 5xx)?

**Decision**: **threshold-based**. `push_subscriptions.failure_count` is
incremented on every 4xx/5xx that isn't 410. When `failure_count >=
PUSH_FAILURE_THRESHOLD` (default 3) AND `last_success_at IS NULL OR
last_success_at < now() - INTERVAL '7 days'`, delete the row at the end of
the next fan-out.

**Why both conditions**: a brand-new subscription with 3 failures and no
successes is dead; an established subscription with 3 transient failures but a
recent success is just flaky. Don't punish the latter.

---

## R-006 — Public-key endpoint vs hardcoded in PWA

**Question**: bake the VAPID public key into the PWA bundle at build time, or
fetch it at runtime from `/api/v1/push/public-key`?

**Decision**: **runtime fetch**.

**Rationale**: key rotation is a one-line backend change; PWAs in the wild
auto-fetch the new key on next subscribe. Bake-time would require a redeploy
of the PWA + cache-bust on every rotation. The endpoint is cheap (in-process
constant, no DB).

**Caching**: the endpoint returns `Cache-Control: public, max-age=3600`.
On rotation, the PO redeploys the API; users get the new key within an hour.

---

## R-007 — Module 003 executor extension

**Question**: how invasive is the change to module 003's executor?

**Decision**: **one line of dispatch logic**, additive.

Module 003's executor (per its `tasks.md` T-012/T-015) has a structure like:

```python
# After committing the cycle update + state_transitions:
if to_state == "FILTERING":
    asyncio.create_task(side_effects.get("director_filter")(chapter_id))
elif to_state == "GENERACION":
    asyncio.create_task(side_effects.get("generation_pipeline")(chapter_id))
```

Module 011 adds:

```python
elif to_state == "ESTRENO":
    fn = side_effects.try_get("push_fanout")    # try_get = no-raise lookup
    if fn:
        asyncio.create_task(fn(chapter_id))
```

This is purely additive. Module 003's tests are unchanged (the new branch is
not exercised by existing tests). The change is documented in
`docs/adr/0006-push-fanout-executor-hook.md`.

---

## R-008 — Real-device smoke is the bar

**Question**: how do we test push reliably?

**Decision**: **real-device smoke replaces formal e2e** for the push leg.

**Why**: Web Push delivery is end-to-end across (our server) → (browser
vendor's push server) → (user's device) → (OS notification surface). The
middle hops are external; we can't reliably mock them. Chromium's headless
push support is incomplete and has been flaky historically.

**Process**:

1. PR ships with mock-based integration tests (the fan-out logic is fully
   tested with `pywebpush` mocked).
2. Before merging the deploy PR, the PO performs the manual smoke from
   `quickstart.md` §10 on their own Android device.
3. The PO files an issue if the smoke fails on iOS (which has tighter PWA
   push constraints).

---

## R-009 — Payload size budget

**Question**: how big can the notification payload be?

**Decision**: cap at **200 chars total** for `title + body`.

**Rationale**: Web Push payloads are encrypted to ~4 KB max post-encryption.
Our shape (FR-010) is comfortably under 1 KB. The 200-char limit on
human-readable text ensures legibility on lock-screens (which truncate around
this length anyway).

---

## R-010 — iOS Safari constraints

**iOS Safari supports Web Push only when the PWA is installed to Home Screen.**
Module 010's iOS install flow makes this discoverable.

If a user tries to toggle notifications on in regular Safari, the permission
prompt won't even appear (Safari refuses). The toggle's UI shows a hint:
"Para activar notificaciones en iPhone, instalá la app primero." Tied to
module 010's iOS install sheet.

---

## Open items

- **OQ-WP-1**: per-notification-type preferences (chapter release vs vote
  reminder vs generation degraded admin alert). Defer.
- **OQ-WP-2**: badge count on the PWA icon (number of unread chapters).
  Requires Badging API; not in MVP.
- **OQ-WP-3**: silent push for state pre-warming (download next chapter's
  assets in advance). Advanced UX; defer.
