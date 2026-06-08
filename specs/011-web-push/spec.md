# Feature Specification: Web Push Notifications

**Feature Branch**: `011-web-push`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `002-auth-invite-flow`, `003-cycle-fsm`, `010-pwa-client`

## Summary

Push notifications via the Web Push Protocol (VAPID). At 12:00 ART, when the FSM
transitions to `ESTRENO`, a background task fans out a notification to every
subscribed user with the new chapter's title. Tap on notification → opens the
PWA at `/today`.

Three endpoints (`POST /push/subscribe`, `DELETE /push/subscriptions/{id}`,
`POST /internal/push/test` for admin), one migration (`push_subscriptions`
table — already shaped in SDD §3.1), one service-worker event handler, one
small dispatcher hook in module 003's executor to invoke the new
`push_fanout` side-effect after ESTRENO transitions.

This module closes the project loop: users no longer need to remember to open
the PWA at 12:00 — they get pinged.

## User Scenarios & Testing

### User Story 1 — User opts in to notifications during onboarding (Priority: P1)

The Settings screen (from module 010) has a "Notificaciones" toggle. Tapping
it requests browser permission and, on grant, registers a push subscription.

**Why this priority**: without subscriptions the fan-out has no audience.

**Independent Test**: redeem an invite, navigate to `/settings`, tap the
toggle, accept the permission prompt, verify a row appears in
`push_subscriptions` and the toggle state persists across reloads.

**Acceptance Scenarios**:

1. **Given** the user is logged in and notification permission is `default`,
   **When** they tap the "Notificaciones" toggle in `/settings`,
   **Then** the browser prompts for permission; on grant, the PWA calls
   `pushManager.subscribe({userVisibleOnly:true, applicationServerKey:
   VAPID_PUBLIC_KEY})`, POSTs the result to `/api/v1/push/subscribe`, and the
   server inserts a `push_subscriptions` row tied to the user. Response 201.

2. **Given** notification permission is `denied`,
   **When** the user views `/settings`,
   **Then** the toggle is in a disabled state with a help link "Cómo
   activarlas en tu navegador". No subscription attempt is made.

3. **Given** the user is already subscribed,
   **When** they toggle off,
   **Then** the PWA calls `subscription.unsubscribe()` (browser-side) AND
   `DELETE /api/v1/push/subscriptions/{id}` (server-side). DB row removed.

### User Story 2 — Fan-out runs at ESTRENO and notifications arrive (Priority: P1)

The 12:00 ART transition to `ESTRENO` fires. Within 60 seconds every
subscribed user receives a push.

**Acceptance Scenarios**:

1. **Given** the cycle is in `PENDING_RELEASE` with a `ready` next chapter and
   3 active push subscriptions exist,
   **When** the 12:00 cron triggers the transition to `ESTRENO`,
   **Then** module 003's executor, after committing the cycle update and
   marking the chapter `live`, spawns the registered `push_fanout` side-effect
   (best-effort — failures here do NOT roll back the transition). The
   side-effect sends 3 web-push requests in parallel and logs the outcomes.

2. **Given** a subscription returns 410 Gone (the user uninstalled the PWA),
   **When** the fan-out tries that endpoint,
   **Then** the row is deleted from `push_subscriptions`, a log event
   `push_subscription_gone {subscription_id}` is emitted, and the fan-out
   continues for other subscriptions.

3. **Given** a subscription returns 429 / 5xx,
   **When** the fan-out tries it,
   **Then** failure_count is incremented; the row stays. After 3 consecutive
   failures across cycles, the row is auto-deleted (cleanup runs on next
   fan-out).

### User Story 3 — User taps notification, lands on /today (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the user receives a notification,
   **When** they tap it,
   **Then** the service-worker `notificationclick` handler focuses an
   existing PWA window if one is open; otherwise opens
   `https://aiplottwist.example/today`. The notification is dismissed.

### User Story 4 — Admin sends a test push (Priority: P2)

The PO wants to verify a specific user's subscription works without waiting
for the next ESTRENO.

**Acceptance Scenarios**:

1. **Given** ADMIN_TOKEN is set,
   **When** the PO calls `POST /api/v1/internal/push/test
   {target_user_public_id: "..."}`,
   **Then** the server sends a one-off push to all of that user's
   subscriptions with title "Prueba — AI Plot Twist" and returns
   `{sent: N, failed: M, gone: K}`.

### User Story 5 — Idempotent fan-out on transition replay (Priority: P2)

Module 003's `state_transitions` UNIQUE protects the FSM from double-fire, but
the fan-out itself should also be idempotent if it's somehow re-invoked.

**Acceptance Scenarios**:

1. **Given** the fan-out for chapter X already ran successfully,
   **When** it's invoked again (manual replay scenario),
   **Then** the `tag` field in the notification (`chapter-<uuid>`) makes the
   browser de-duplicate on the device side; on the server side, an
   `idempotency_keys` row keyed by `push_fanout:<chapter_uuid>` short-
   circuits the second invocation.

### Edge Cases

- **VAPID_PRIVATE_KEY not set**: subscribe endpoint returns 503
  `vapid_not_configured`; fan-out logs and no-ops.
- **User has 5 subscriptions** (multiple devices/browsers): each gets the
  push independently. UNIQUE on `push_subscriptions.endpoint` deduplicates
  exact identical endpoints (e.g., the user re-subscribes from the same
  browser before unsubscribing — the new subscription replaces the old via
  UPSERT).
- **Network failure during subscribe**: PWA retries up to 3× with backoff;
  surfaces error if all fail.
- **Subscription belongs to a banned user**: fan-out skips (joins
  `push_subscriptions` with `users WHERE NOT is_banned`).
- **Push payload too large**: VAPID protocol caps at ~4 KB encrypted. We
  cap our payload at 200 chars to leave headroom.
- **User clears site data**: SW unregisters, subscription becomes
  invalid; next push returns 410, server cleans up. No user action needed.
- **Time-skewed clock on user device**: JWT auth on subscribe handles
  ±60 s leeway (inherited from module 002).
- **Kill-switch active during ESTRENO transition**: the transition itself
  doesn't run (per module 003 + 004 behavior); fan-out is never invoked.
  No spurious notifications during maintenance.

## Requirements

### Functional Requirements

- **FR-001**: `push_subscriptions` table per SDD §3.1 (verified in
  [data-model.md](./data-model.md)).
- **FR-002**: `pnpm generate-vapid` CLI generates a fresh VAPID keypair and
  prints both keys; refuses to overwrite unless `--force`.
- **FR-003**: Settings:
  - `VAPID_PUBLIC_KEY` (env; PWA-readable via a `/api/v1/push/public-key`
    helper).
  - `VAPID_PRIVATE_KEY` (Fly secret).
  - `VAPID_SUBJECT` (env; e.g., `mailto:po@aiplottwist.example`).
  - `PUSH_FANOUT_TIMEOUT_S` (default 60).
  - `PUSH_FAILURE_THRESHOLD` (default 3 consecutive failures → delete row).
- **FR-004**: `POST /api/v1/push/subscribe` requires JWT. Body:
  `{endpoint, keys: {p256dh, auth}, user_agent?}`. Idempotent via UPSERT on
  `endpoint`. Returns `{subscription_id}`.
- **FR-005**: `DELETE /api/v1/push/subscriptions/{id}` requires JWT.
  Ownership check (`push_subscriptions.user_id == jwt.user_id`). Soft
  semantics: hard delete (push subscriptions are not user content; cleared
  state is fine).
- **FR-006**: `GET /api/v1/push/public-key` (unauth) returns the public VAPID
  key for the PWA to subscribe.
- **FR-007**: `POST /api/v1/internal/push/test` requires `ADMIN_TOKEN`.
  Body: `{target_user_public_id: UUID}`. Sends a hardcoded test payload
  ("Prueba — AI Plot Twist") to that user's subscriptions. Returns
  `{sent, failed, gone}` breakdown.
- **FR-008**: `push_fanout(chapter_id: int) -> None` side-effect:
  - Joins `push_subscriptions` with `users` (excluding banned).
  - Composes the payload (see FR-010).
  - Calls `pywebpush.webpush(...)` per subscription, `asyncio.gather` with
    `PUSH_FANOUT_TIMEOUT_S` soft deadline.
  - Per result:
    - 201/204 → log `push_sent {subscription_id}`, reset failure_count.
    - 404/410 → delete row, log `push_subscription_gone`.
    - 429/5xx → increment failure_count, log `push_send_failed`.
  - Cleanup pass: delete rows with `failure_count >= PUSH_FAILURE_THRESHOLD`.
- **FR-009**: Module 003's executor extended with a tiny dispatcher hook:
  after a successful transition to `ESTRENO`, if a side-effect
  named `push_fanout` is registered, spawn it as a `BackgroundTask`. The
  transition's success is NOT contingent on the fan-out succeeding
  (best-effort; constitution Gate 2 idempotency preserved separately).
- **FR-010**: Notification payload shape (sent by backend):
  ```json
  {
    "title": "AI Plot Twist — Día 8",
    "body": "Hoy: Lo que había detrás del espejo",
    "icon": "/icons/icon-192.png",
    "badge": "/icons/badge-72.png",
    "tag": "chapter-<chapter_public_id>",
    "data": { "chapter_public_id": "...", "url": "/today" }
  }
  ```
  `tag` ensures device-side dedup if the user has multiple subscriptions for
  the same browser.
- **FR-011**: Idempotency: an `idempotency_keys` row with key
  `push_fanout:<chapter_public_id>` is written on first successful fan-out.
  Re-invocation (manual replay or test admin) checks this row and short-
  circuits with `{sent: 0, skipped_idempotent: true}` unless `--force`
  param is passed (admin endpoint only).
- **FR-012**: Service-worker `push` event handler:
  ```js
  self.addEventListener('push', (event) => {
    const data = event.data?.json() ?? {};
    event.waitUntil(self.registration.showNotification(
      data.title ?? 'AI Plot Twist',
      { body: data.body, icon: data.icon, badge: data.badge,
        tag: data.tag, data: data.data }
    ));
  });
  ```
- **FR-013**: Service-worker `notificationclick` event handler:
  ```js
  self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = event.notification.data?.url ?? '/today';
    event.waitUntil((async () => {
      const clients = await self.clients.matchAll({type:'window'});
      for (const c of clients) {
        if (c.url.endsWith(url)) { c.focus(); return; }
      }
      await self.clients.openWindow(url);
    })());
  });
  ```
- **FR-014**: PWA "Notificaciones" toggle in `/settings`:
  - Shows current `Notification.permission` state.
  - On `default`: toggle activates → request permission → on grant,
    subscribe.
  - On `granted` + subscribed: toggle is on; toggling off unsubscribes.
  - On `denied`: disabled state with help link.
- **FR-015**: Logging:
  - `push_subscribe_received {user_id, endpoint_host, new_or_replace}`.
  - `push_fanout_started {chapter_id, total_subs}`.
  - `push_sent {subscription_id}`.
  - `push_subscription_gone {subscription_id}`.
  - `push_send_failed {subscription_id, status, failure_count}`.
  - `push_fanout_completed {chapter_id, sent, gone, failed, duration_ms}`.

### Non-Functional Requirements

- **NFR-001**: Fan-out completes within `PUSH_FANOUT_TIMEOUT_S` (60 s) for
  up to 100 subscriptions. Parallel sends via `asyncio.gather`.
- **NFR-002**: `POST /push/subscribe` p95 < 200 ms.
- **NFR-003**: Notification arrives on user device within 30 s p95 of
  ESTRENO transition (driven by browser push servers; the part we control
  is ≤ 1 s).
- **NFR-004**: VAPID generation deterministic with a seed (for tests only;
  prod uses OS RNG).

### Out of Scope (for this feature)

- Per-user notification preferences (e.g., "only on weekends"). Single
  binary toggle.
- Per-notification type (chapter release vs vote reminder). Only chapter
  release in MVP.
- Rich notifications (images, action buttons beyond default tap). Future.
- Quiet hours by user time zone.
- Aggregation when the user has multiple devices ("you have 3 unread").
- SMS / email as alternative channels.
- Telegram bot push (would require a different transport entirely; future
  module if any).
- Read-receipts (push API doesn't expose this).
