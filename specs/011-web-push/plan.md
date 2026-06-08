# Implementation Plan: Web Push Notifications

**Branch**: `011-web-push` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `002-auth-invite-flow`, `003-cycle-fsm`, `010-pwa-client`

## Summary

Close the project loop with push notifications via the Web Push Protocol
(VAPID). Backend: `push_subscriptions` table, three endpoints (one auth, one
admin, one public for the VAPID public key), a fan-out side-effect spawned
after ESTRENO. Frontend: settings toggle, subscription registration, two SW
event handlers. CLI: VAPID key generation.

This module reuses module 003's side-effect DI pattern. Module 003's executor
needs a one-line extension: on successful ESTRENO transition, look up the
optional `push_fanout` registered side-effect and spawn it.

## Technical Context

**Languages/Versions**: Python 3.11; TypeScript 5.4+.
**New API dependencies**:
- `pywebpush ~=2.0` (Web Push Protocol + VAPID).
- `cryptography ~=42.0` (transitive of pywebpush; explicit pin for security).
- `py-vapid ~=1.9` (key generation, used by CLI).
**New web dependencies**: none.
**Storage**: 1 new table (`push_subscriptions`).
**Testing**: pytest for backend (mock pywebpush); Playwright for PWA permission
flow (Chromium supports auto-grant); manual real-device smoke for push delivery.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-004.
**Constraints**: zero cost (no paid notification service); Web Push Protocol is
peer-to-peer between server and browser push services (Mozilla autopush, Google
FCM, Apple APN) — all free.
**Scale/Scope**: ≤ 100 subscriptions in closed beta; ≤ 1 fan-out / day.

## Constitution Check

### Gate 1 — Zero-cost
- [x] Web Push is RFC-standard, no paid service needed.

### Gate 2 — Idempotency
- [x] Subscribe = UPSERT on `endpoint`.
- [x] Unsubscribe = DELETE, naturally idempotent.
- [x] Fan-out idempotent on `push_fanout:<chapter_uuid>` key.
- [x] Device-side dedup via notification `tag`.

### Gate 3 — TZ anchoring
- [x] ESTRENO at 12:00 ART (inherited from module 003).
- [x] Notifications timestamped at server time (UTC).

### Gate 4 — Provider abstraction
- [x] `pywebpush` is the standards-based "provider"; no vendor SDK. Cleanly
      swappable to `py-vapid + httpx` if needed (no abstraction layer required
      — the surface is small).

### Gate 5 — Determinism
- [x] Notification payload is deterministic given `chapter_id`.

### Gate 6 — Spanish UI / English code
- [x] Identifiers English. Notification text Spanish.

### Gate 7 — Soft delete
- [x] `push_subscriptions` rows are **hard-deleted** on `Gone` (410) and on
      user unsubscribe. These are not user-authored content — they're
      device-bound credentials. Documented as an explicit exception:
      Gate 7 applies to user-authored content; transient device tokens are
      out of scope.

### Gate 8 — Tests from day one
- [x] Unit: VAPID key generation, payload composition, fan-out result
      aggregation.
- [x] Integration: subscribe, unsubscribe, test-admin endpoint,
      fan-out-with-mock-pywebpush, cleanup-on-410.
- [x] PWA: vitest for settings toggle state machine; Playwright for SW
      registration + permission grant.
- [x] Real-device smoke: receive a notification on Android + iOS.

### Gate 9 — Trust boundaries
- [x] Subscribe requires JWT; ownership enforced on DELETE.
- [x] Public key endpoint exposes only the public key (safe).
- [x] Admin test endpoint requires ADMIN_TOKEN.
- [x] Payload sanitized: title + body limited to 200 chars; URL whitelisted
      to `/today` and `/vote` only.

### Gate 10 — Observability
- [x] Six structured events documented in FR-015.

## Project Structure

```text
specs/011-web-push/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── push.yaml
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

```text
apps/api/
├── alembic/versions/
│   └── 0009_push_subscriptions.py        ← NEW
├── app/
│   ├── domain/
│   │   ├── push_payload.py               ← NEW (notification composition)
│   │   └── push_fanout.py                ← NEW (orchestrator)
│   ├── infra/
│   │   ├── push_subscriptions_repo.py    ← NEW
│   │   └── webpush_sender.py             ← NEW (wraps pywebpush)
│   ├── api/
│   │   ├── push.py                       ← NEW (subscribe, unsubscribe, public-key)
│   │   └── internal_push_test.py         ← NEW (admin)
│   ├── scripts/
│   │   └── generate_vapid.py             ← NEW (CLI)
│   ├── settings.py                       ← MODIFIED (VAPID_*, PUSH_*)
│   ├── domain/cycle_executor.py          ← MODIFIED (1-line dispatcher hook)
│   └── main.py                           ← MODIFIED (DI register push_fanout)
└── tests/
    ├── unit/
    │   ├── test_push_payload.py
    │   ├── test_vapid_generation.py
    │   └── test_push_fanout_aggregation.py
    ├── integration/
    │   ├── test_push_subscribe.py
    │   ├── test_push_unsubscribe.py
    │   ├── test_push_public_key.py
    │   ├── test_push_admin_test.py
    │   ├── test_push_fanout_e2e.py
    │   └── test_push_cleanup_410.py
    └── live/
        └── test_real_push_smoke.py       ← @pytest.mark.live (requires real device)

apps/web/
├── src/
│   ├── lib/
│   │   ├── push-store.ts                 ← NEW (Svelte 5 runes)
│   │   ├── push-api.ts                   ← NEW
│   │   └── components/
│   │       └── PushToggle.svelte         ← NEW (replaces stub in module 010)
│   ├── routes/
│   │   └── settings.svelte               ← MODIFIED (real toggle wired)
│   └── service-worker.ts                 ← MODIFIED (push + notificationclick handlers)
└── tests/
    ├── push-store.test.ts
    ├── push-toggle.test.ts
    └── e2e/
        └── push-permission-flow.spec.ts
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- `pywebpush` chosen over rolling our own VAPID JWT signer.
- Hard delete of push subscriptions on `Gone` (documented Gate 7 exception).
- Idempotency on fan-out via `idempotency_keys` table.
- Module 003 executor extension: one-line dispatcher addition, not a refactor.
- Real-device smoke replaces formal e2e for the push leg.

## Phase 1 — Design Artefacts

- [contracts/push.yaml](./contracts/push.yaml).
- [data-model.md](./data-model.md).
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001** — Migration `0009_push_subscriptions.py`.
2. **T-002** — `generate-vapid` CLI.
3. **T-003** — `PushSubscriptionsRepo`.
4. **T-004** — `WebPushSender` infra.
5. **T-005** — `push_payload` composition + `push_fanout` orchestrator.
6. **T-006** — Three HTTP endpoints (subscribe, unsubscribe, public-key).
7. **T-007** — Admin test endpoint.
8. **T-008** — Module 003 executor extension + DI registration.
9. **T-009** — PWA push-store + push-api.
10. **T-010** — `PushToggle.svelte` + Settings integration.
11. **T-011** — Service-worker handlers.
12. **T-012** — Real-device smoke.
13. **T-013** — Deploy + observe one ESTRENO.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-WP1** | iOS Safari support is gated to PWAs installed to Home Screen (no support in normal Safari) | Documented as a known limitation; install flow from module 010 enables it. |
| **R-WP2** | Push servers (FCM, autopush) silently drop | Browsers handle delivery; we see only the immediate HTTP response. 410 cleanup handles long-term staleness. |
| **R-WP3** | VAPID keypair rotation invalidates all subscriptions | Documented; rotation requires a forced re-subscribe flow (PWA detects mismatch and re-subscribes silently). Not implemented in MVP. |
| **R-WP4** | Fan-out blocks on slow endpoints | `asyncio.gather` with `asyncio.wait_for(per-call timeout 10s)`; soft total deadline `PUSH_FANOUT_TIMEOUT_S = 60`. |
| **R-WP5** | Notification payload XSS via display strings | Title/body are server-generated from chapter title (already sanitized by the scriptwriter Pydantic model + visual_prompt validator). No user-supplied text in notifications. |
| **R-WP6** | The 1-line module 003 extension breaks 003's tests | Extension is purely additive; tests reviewed in this PR. ADR-0006 documents the cross-module change. |

## Post-Conditions

After merge — and after PO smoke-tests on their phone — the loop is **truly
autonomous**:

- 12:00 ART → ESTRENO → push to every subscriber → user taps → opens PWA at
  `/today` → reads the new chapter → submits a twist → votes → next ESTRENO.

The product is shippable to the closed family-friends cohort.
