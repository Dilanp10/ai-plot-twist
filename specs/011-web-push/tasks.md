# Task Breakdown: Web Push

**Branch**: `011-web-push` | **Date**: 2026-06-07

---

## Phase 0 — Migration + VAPID (2 PRs)

### T-001 — `0009_push_subscriptions` → 007-merged
**Files**:
- `apps/api/alembic/versions/0009_push_subscriptions.py`
- `apps/api/tests/integration/test_migrations.py::test_0009_upgrade_downgrade`

### T-002 — `generate-vapid` CLI → 001-merged [P]
**Files**:
- `apps/api/app/scripts/generate_vapid.py`
- `apps/api/tests/unit/test_vapid_generation.py`
- root + apps/api `package.json` delegation

**Behavior**: prints keypair to stdout; with `--out FILE` appends to a file
with safe overwrite check; with `--seed N` (test only) produces deterministic
output.

---

## Phase 1 — Backend infra + domain (4 PRs)

### T-003 — `PushSubscriptionsRepo` → T-001 [P]
**Files**:
- `apps/api/app/infra/push_subscriptions_repo.py`
- `apps/api/tests/integration/test_push_subscriptions_repo.py`

**Methods**:
- `upsert(user_id, endpoint, p256dh, auth, ua) -> int` (returns id)
- `delete_by_id_for_user(id, user_id) -> bool`
- `list_active_for_user(user_id) -> list[Subscription]`
- `list_active_all() -> list[Subscription]` (excludes banned users)
- `mark_success(id)` / `mark_failure(id)` / `bulk_delete(ids)`
- `cleanup_stale(threshold) -> int` (returns rows deleted)

### T-004 — `WebPushSender` (pywebpush wrapper) → T-002 [P]
**Files**:
- `apps/api/app/infra/webpush_sender.py`
- `apps/api/tests/unit/test_webpush_sender.py`

**API**:
```python
class WebPushSender:
    def __init__(self, vapid_private_key: str, vapid_subject: str): ...
    async def send(self, subscription: Subscription, payload: bytes,
                   timeout: float = 10.0) -> SendResult: ...
```

`SendResult` = `success | gone | failed`. Wraps `pywebpush.webpush` in
`run_in_executor`. Translates HTTP status: 201/204 → success, 404/410 → gone,
else → failed.

### T-005 — `push_payload.compose` → 004-merged [P]
**Files**:
- `apps/api/app/domain/push_payload.py`
- `apps/api/tests/unit/test_push_payload.py`

**API**:
```python
def compose_chapter_notification(chapter: Chapter, season: Season) -> dict: ...
def compose_test_notification() -> dict: ...
```

Returns JSON-ready dict per FR-010. Enforces ≤ 200 chars total title+body.

### T-006 — `push_fanout` orchestrator → T-003, T-004, T-005
**Files**:
- `apps/api/app/domain/push_fanout.py`
- `apps/api/tests/integration/test_push_fanout_e2e.py`
- `apps/api/tests/integration/test_push_cleanup_410.py`

**Behavior**: as documented in spec + data-model. Idempotency, parallel sends,
result aggregation, cleanup pass.

---

## Phase 2 — HTTP endpoints (3 PRs)

### T-007 — `GET /push/public-key` → 001-merged [P]
**Files**:
- `apps/api/app/api/push.py`
- `apps/api/tests/integration/test_push_public_key.py`

### T-008 — `POST /push/subscribe` and `DELETE /push/subscriptions/{id}` → T-003 [P]
**Files**:
- `apps/api/app/api/push.py` (extend)
- `apps/api/tests/integration/test_push_subscribe.py`
- `apps/api/tests/integration/test_push_unsubscribe.py`

### T-009 — `POST /internal/push/test` → T-006
**Files**:
- `apps/api/app/api/internal_push_test.py`
- `apps/api/tests/integration/test_push_admin_test.py`

---

## Phase 3 — FSM integration (1 PR)

### T-010 — Module 003 executor hook + DI registration → T-006, 003-merged
**Files**:
- `apps/api/app/domain/cycle_executor.py` (MODIFIED — 1 line)
- `apps/api/app/main.py` (DI register `push_fanout`)
- `apps/api/tests/integration/test_executor_push_dispatch.py`
- `docs/adr/0006-push-fanout-executor-hook.md`
- `docs/adr/0007-push-subscription-hard-delete.md`
- `.specify/memory/constitution.md` (Gate 7 carve-out, PATCH version)

**Behavior**: per research R-007. Test asserts `push_fanout` is invoked on
ESTRENO when registered; not invoked on other transitions.

---

## Phase 4 — PWA (3 PRs)

### T-011 — `push-api.ts` + `push-store.ts` → 010-merged
**Files**:
- `apps/web/src/lib/push-api.ts`
- `apps/web/src/lib/push-store.ts`
- `apps/web/tests/push-api.test.ts`
- `apps/web/tests/push-store.test.ts`

**Store API**:
```ts
export const pushStore = {
  permission: $state<NotificationPermission>('default'),
  subscription: $state<PushSubscription | null>(null),
  serverKnowsAboutMe: $state<boolean>(false),
  init(): Promise<void>,
  enable(): Promise<void>,       // requests + subscribes
  disable(): Promise<void>,      // unsubscribes + deletes server-side
};
```

### T-012 — `PushToggle.svelte` + Settings wire-up → T-011
**Files**:
- `apps/web/src/lib/components/PushToggle.svelte`
- `apps/web/src/routes/settings.svelte` (MODIFIED — replace 010's stub)
- `apps/web/tests/push-toggle.test.ts`

### T-013 — SW handlers → T-011 [P]
**Files**:
- `apps/web/src/service-worker.ts` (extend with `push` and `notificationclick`
  listeners per FR-012, FR-013)
- `apps/web/tests/e2e/push-permission-flow.spec.ts` (Chromium with auto-grant)

---

## Phase 5 — Real-device smoke + deploy (2 PRs)

### T-014 — Real-device smoke → all prior
**Files**:
- `specs/011-web-push/quickstart.md` (verified on Android by PO)

**Done when**: the §10 smoke completes end-to-end on a real Android device.

### T-015 — Deploy + observe + close project → T-014
**Files**:
- `specs/README.md` (mark 011 done; project status: **MVP closed-beta ready**)

---

## Done-when (module-level acceptance)

1. All 15 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. PO has completed the real-device smoke and signed off.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Migration + VAPID | T-001..T-002 | 1 |
| 1 — Backend infra | T-003..T-006 | 3 |
| 2 — Endpoints | T-007..T-009 | 1.5 |
| 3 — FSM integration | T-010 | 1 |
| 4 — PWA | T-011..T-013 | 2.5 |
| 5 — Smoke + deploy | T-014..T-015 | 1 |
| **Total** | 15 tasks | **≈ 10 days** |

Buffer +25% for iOS quirks and pywebpush edge cases → **plan for 12 working
days**.
