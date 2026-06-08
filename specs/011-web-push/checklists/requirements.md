# Requirements Checklist: Web Push

**Branch**: `011-web-push` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — `push_subscriptions` table per data-model.md.
      Migration up + downgrade tested.
- [ ] **FR-002** — `pnpm generate-vapid` produces a valid keypair; refuses
      overwrite without `--force`. Tested.
- [ ] **FR-003** — All settings exposed via env with documented defaults.
- [ ] **FR-004** — `POST /push/subscribe` UPSERTs on `endpoint`. Re-subscribe
      from same browser replaces.
- [ ] **FR-005** — `DELETE /push/subscriptions/{id}` requires JWT + ownership.
      404 for non-existent; 403 for cross-user.
- [ ] **FR-006** — `GET /push/public-key` returns key with 1-hour cache.
      Returns 503 if `VAPID_PRIVATE_KEY` missing.
- [ ] **FR-007** — Admin test endpoint sends to specified user's subs only.
      `force=true` bypasses idempotency.
- [ ] **FR-008** — Fan-out: parallel pywebpush calls with per-call 10 s
      timeout; total soft deadline 60 s; per-result actions per spec.
- [ ] **FR-009** — Module 003 executor extension: 1-line addition; existing
      003 tests still green; new test asserts push_fanout spawned on
      ESTRENO when registered.
- [ ] **FR-010** — Notification payload matches the documented shape;
      title + body ≤ 200 chars total.
- [ ] **FR-011** — Idempotency: `push_fanout:<uuid>` key prevents re-send;
      `force=true` admin override works.
- [ ] **FR-012** — SW `push` handler shows notification with all documented
      fields.
- [ ] **FR-013** — SW `notificationclick` focuses existing window or opens
      `/today`. URL whitelist enforced.
- [ ] **FR-014** — Settings toggle handles all three `Notification.permission`
      states with correct UX.
- [ ] **FR-015** — All 6 structured log events emitted with documented keys.

## Non-Functional Requirements

- [ ] **NFR-001** — 100 subs fan-out in ≤ 60 s with FakeWebPush (1 s/call).
- [ ] **NFR-002** — `/push/subscribe` p95 < 200 ms.
- [ ] **NFR-003** — End-to-end notification delivery ≤ 30 s on a Pixel
      (measured manually in PO smoke).
- [ ] **NFR-004** — VAPID generation with `--seed` deterministic
      (test only); without seed, uses OS RNG.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — No paid services. Web Push is RFC-standard.
- [ ] **Gate 2 — Idempotency** — Subscribe UPSERT, Unsubscribe DELETE,
      Fan-out idempotent on chapter key, device-side `tag` dedup.
- [ ] **Gate 3 — TZ anchoring** — ESTRENO at 12:00 ART inherited from 003.
- [ ] **Gate 4 — Provider abstraction** — `pywebpush` is the
      standards-based "provider"; no vendor SDK.
- [ ] **Gate 5 — Determinism** — Payload composition deterministic for
      given chapter.
- [ ] **Gate 6 — Spanish / English** — Code English; notification text
      Spanish.
- [ ] **Gate 7 — Soft delete** — **Documented exception** (ADR-0007) for
      `push_subscriptions`: hard delete. Other tables unaffected.
- [ ] **Gate 8 — Tests from day one** — Mock-based unit + integration;
      real-device smoke as the gate.
- [ ] **Gate 9 — Trust boundaries** — JWT on subscribe; ownership on
      delete; ADMIN_TOKEN on test; URL whitelisted in SW click handler.
- [ ] **Gate 10 — Observability** — Six events live.

## Real-device smoke

- [ ] PO completed the §10 smoke on Android.
- [ ] PO completed the §10 smoke on iOS (PWA installed to Home Screen).

## Documentation

- [ ] Quickstart walked end-to-end.
- [ ] `docs/adr/0006-push-fanout-executor-hook.md` exists.
- [ ] `docs/adr/0007-push-subscription-hard-delete.md` exists.
- [ ] Constitution Gate 7 amended (PATCH version bump) to add the
      push_subscriptions carve-out.
- [ ] `specs/README.md` marks 011 `done` → MVP closed-beta ready.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO) — must complete real-device smoke before sign-off.
