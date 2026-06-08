# Data Model: PWA Client

**Branch**: `010-pwa-client` | **Date**: 2026-06-07

**No new DB tables, no migrations.** This module is overwhelmingly frontend.

The one backend endpoint introduced (`/internal/client-log`) is **stateless** —
it accepts a payload, structured-logs it, returns 202. No persistence.

What this file documents instead:

1. **Client-side storage layout** (IndexedDB + localStorage).
2. **Client log payload contract** (mirrored in `contracts/client-log.yaml`).
3. **CSP header values** (live in `apps/web/public/_headers`).

---

## Client-side storage

### IndexedDB

Database: `aiplottwist` (created by module 002).
Stores used by module 010: **only `auth` (from 002)** + a **new `prefs` store**.

| Store | Keys | Notes |
|---|---|---|
| `auth` | `jwt`, `device_secret` | Owned by module 002. Module 010 only clears it on sign-out. |
| `prefs` (NEW) | `last_seen_chapter_day`, `install_prompt_dismissed_at` | Used by install-flow gating. Optional; absence is safe. |

### localStorage

| Key | Owner | Purpose |
|---|---|---|
| `apt.iosSheetDismissedAt` | Install flow | Sticky for 14 days. |
| `apt.swUpdateToastDismissedAt` | SW update notifier | Per release. |
| `apt.lastRouteByCycleState` | Route resolver | Diagnostic; not load-bearing. |

All keys namespaced under `apt.` to coexist with other PWAs on the same
origin (defensive, though we own the origin).

### Sign-out wipes

Per research R-011: IndexedDB.aiplottwist (full), localStorage (all `apt.*`
keys), SW registrations, `caches.keys()`.

---

## Client log payload (contract)

See `contracts/client-log.yaml` for the canonical schema. Fields:

| Field | Type | Required | Max bytes |
|---|---|---|---|
| `event` | enum | yes | 32 |
| `message` | string | no | 500 |
| `stack` | string | no | 3000 |
| `route` | string | no | 200 |
| `user_agent` | string | yes | 200 |
| `app_version` | string | yes | 50 |
| `timestamp` | ISO 8601 | yes | 32 |
| `extra` | object | no | 500 |

Total payload ≤ 4 KB. Server rejects 413 on over-limit.

### `event` enum

| Value | Trigger |
|---|---|
| `unhandled_error` | `window.error` handler |
| `unhandled_rejection` | `window.unhandledrejection` handler |
| `boundary_caught` | `<ErrorBoundary>` Svelte component |
| `csp_violation` | CSP report-uri (Phase 2) |
| `custom` | Manual call from app code (e.g., diagnostics) |

---

## CSP header

Set in `apps/web/public/_headers` (Cloudflare Pages):

```
/*
  Content-Security-Policy: default-src 'self'; img-src 'self' https://assets.aiplottwist.example data: blob:; media-src 'self' https://assets.aiplottwist.example; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' https://api.aiplottwist.example; report-uri https://api.aiplottwist.example/api/v1/internal/client-log
  Strict-Transport-Security: max-age=31536000; includeSubDomains
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  X-Content-Type-Options: nosniff
```

**Phase 1 (rollout)**: replace `Content-Security-Policy` with
`Content-Security-Policy-Report-Only` for 7 days; then flip per research R-007.

---

## Server-side log shape

When `/internal/client-log` accepts a payload, the backend emits:

```jsonc
{
  "event": "client_log_received",
  "client_event": "unhandled_rejection",   // from body.event
  "client_message": "TypeError: …",         // from body.message (truncated to 500)
  "client_stack": "...",
  "client_route": "/vote",
  "client_user_agent": "Mozilla/5.0 ...",
  "client_app_version": "1.2.3-abc123",
  "client_timestamp": "2026-06-08T12:42:11Z",
  "request_id": "uuid",                     // server-assigned
  "ip_hash": "sha256_short",                // for ops grouping; never raw IP
  "received_at": "..."
}
```

No DB row. Greppable in Fly logs by `client_log_received`. Per-route error rates
computable via `| jq` post-hoc.

---

## What this module does NOT touch

- Any business table.
- The `idempotency_keys` table.
- The `rate_limit_buckets` table is **read** by the IP-rate-limit for client-log
  but not extended.
