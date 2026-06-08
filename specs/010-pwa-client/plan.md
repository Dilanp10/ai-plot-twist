# Implementation Plan: PWA Polish, Install, Offline, A11y

**Branch**: `010-pwa-client` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `002-auth-invite-flow`, `004-chapters-content`,
                `005-twists-submission`, `007-voting`

## Summary

Frontend wrap-up: app shell with navigation, finalized service-worker strategy,
install flow for both Android and iOS, settings screen with sign-out,
accessibility minimums, loading skeletons, error boundaries, performance budget
in CI, and a minimal client-log backend endpoint. No new business logic; one new
endpoint; zero new DB tables.

## Technical Context

**Languages/Versions**: TypeScript 5.4+, Svelte 5 (unchanged from 002).
**New API dependencies**: none.
**New web dependencies**:
- `svelte-spa-router ~=4.0` (or `tinro` if smaller).
- `workbox-window ~=7.1` (peer dep of `vite-plugin-pwa`).
- `@lhci/cli` (devDep) for Lighthouse CI.
**Storage**: none new.
**Testing**:
- vitest for unit (stores, components).
- Playwright for e2e flows (install prompt, sign-out, error boundary).
- Lighthouse CI for performance + a11y budgets.
- Manual smoke on real Android (Pixel) + iOS (iPhone) device before
  declaring done.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-005.
**Constraints**: bundle budget 80 KB gz main route; SW precache 200 KB gz.
**Scale/Scope**: 10–40 concurrent users in closed beta.

## Constitution Check

### Gate 1 — Zero-cost
- [x] No new services. Lighthouse CI runs on GH Actions free.

### Gate 2 — Idempotency
- [x] Install prompts idempotent (second tap is no-op).
- [x] Client-log endpoint accepts dupes (no DB write).
- [x] Sign-out is idempotent (already-cleared state → no-op).

### Gate 3 — TZ anchoring
- [x] Countdown components reuse module 004's `windows` ISO strings; convert
      to ART for display via the `Intl.DateTimeFormat({timeZone: 'America/Argentina/Buenos_Aires'})`
      API.

### Gate 4 — Provider abstraction
- [x] N/A (no LLM/T2I).

### Gate 5 — Determinism
- [x] Routing decision based on `cycle_state` is deterministic.
- [x] Animations honor `prefers-reduced-motion`.

### Gate 6 — Spanish UI / English code
- [x] **Central to this module.** All strings reviewed; identifiers English.
      `i18n` not implemented but the structure (strings in `src/lib/strings.ts`
      keyed by id) is in place for future translation.

### Gate 7 — Soft delete
- [x] Sign-out is a client-side wipe of credentials. The user row in DB is
      NOT deleted (Gate 7 preserved).

### Gate 8 — Tests from day one
- [x] Vitest unit for all new components.
- [x] Playwright e2e for install flow + sign-out + error boundary +
      offline.
- [x] Lighthouse CI as the perf + a11y gate.

### Gate 9 — Trust boundaries
- [x] CSP set via Cloudflare Pages `_headers`. Tested in CI.
- [x] `client-log` endpoint accepts limited payload (4 KB), IP-rate-limited,
      no DB writes.
- [x] Sign-out wipes credentials thoroughly (IndexedDB + localStorage + SW
      unregister).

### Gate 10 — Observability
- [x] Client-side errors POST to `/internal/client-log`.
- [x] Backend logs `client_log_received` with all fields.

## Project Structure

```text
specs/010-pwa-client/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── client-log.yaml
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

```text
apps/api/
├── app/
│   └── api/
│       └── internal_client_log.py        ← NEW (single endpoint)
└── tests/
    └── integration/
        └── test_client_log_endpoint.py

apps/web/
├── public/
│   ├── manifest.webmanifest              ← FINALIZED
│   ├── icons/
│   │   ├── icon-192.png
│   │   ├── icon-512.png
│   │   ├── icon-192-maskable.png
│   │   ├── icon-512-maskable.png
│   │   ├── apple-touch-icon-180.png
│   │   └── splash.png
│   └── _headers                          ← NEW (Cloudflare Pages CSP)
├── src/
│   ├── App.svelte                        ← REPLACED (app shell)
│   ├── lib/
│   │   ├── strings.ts                    ← NEW (i18n-ready, Spanish strings)
│   │   ├── route-resolver.ts             ← NEW (cycle_state → route)
│   │   ├── install-prompt.ts             ← NEW (Android beforeinstallprompt)
│   │   ├── ios-install-sheet.ts          ← NEW (iOS detection + sheet)
│   │   ├── error-boundary.svelte         ← NEW
│   │   ├── client-logger.ts              ← NEW (throttle + POST)
│   │   ├── sw-update-notifier.ts         ← NEW (workbox-window)
│   │   ├── theme-tokens.css              ← NEW (CSS custom properties)
│   │   └── components/
│   │       ├── AppShell.svelte
│   │       ├── BottomNav.svelte
│   │       ├── TopBar.svelte
│   │       ├── Skeleton.svelte
│   │       ├── InstallPromptCard.svelte
│   │       ├── IosInstallSheet.svelte
│   │       └── MaintenanceCard.svelte
│   └── routes/
│       ├── onboarding.svelte             ← polished (from 002)
│       ├── today.svelte                  ← polished
│       ├── vote.svelte                   ← polished
│       ├── me.svelte                     ← polished (from 005)
│       └── settings.svelte               ← NEW
└── tests/
    ├── unit/
    │   ├── route-resolver.test.ts
    │   ├── client-logger.test.ts
    │   └── install-prompt.test.ts
    └── e2e/
        ├── install-android.spec.ts       ← Chromium with PWA install enabled
        ├── install-ios.spec.ts           ← UA spoofed; verifies sheet shows
        ├── sign-out.spec.ts
        ├── offline-flow.spec.ts
        ├── error-boundary.spec.ts
        └── a11y-keyboard.spec.ts

.github/workflows/
└── lighthouse-ci.yml                     ← NEW
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- `svelte-spa-router` for hash routing (smallest, simplest).
- Workbox via `vite-plugin-pwa` for service worker.
- CSS variables instead of a CSS-in-JS lib (zero JS overhead).
- System font stack (Inter as fallback, no web font fetch).
- Client log unauthenticated, IP-rate-limited, no DB.
- iOS install detection via UA + `navigator.standalone`.

## Phase 1 — Design Artefacts

- [contracts/client-log.yaml](./contracts/client-log.yaml).
- [data-model.md](./data-model.md).
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001..T-003** — App shell, routing, top/bottom nav.
2. **T-004** — Service worker strategy finalization.
3. **T-005..T-006** — Install flows (Android + iOS).
4. **T-007** — Settings route + sign-out.
5. **T-008..T-009** — Skeleton + error boundary.
6. **T-010** — Client logger + backend endpoint.
7. **T-011** — Accessibility pass (manual + Lighthouse).
8. **T-012** — Performance budget (CSS, lazy routes, bundle audit).
9. **T-013** — Lighthouse CI workflow.
10. **T-014..T-015** — e2e tests + manual device smoke.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-PW1** | Lighthouse perf budget too tight to meet | Profile early; pre-set budgets to current numbers + 10 % headroom; iterate |
| **R-PW2** | iOS Safari quirks with IndexedDB | Tested manually; fallback to `localStorage` if IDB fails (degraded but functional) |
| **R-PW3** | CSP breaks legitimate features | Start in `Content-Security-Policy-Report-Only` mode; ship enforcement after a week of zero violations |
| **R-PW4** | SW update strategy confuses users | The "Actualización disponible" toast is opt-in; auto-update on next cold start |
| **R-PW5** | Bundle creep from new components | Bundle audit step in CI: fails if main route > 80 KB gz |
| **R-PW6** | Tap target accessibility on small screens | 44×44 px enforced in component CSS; e2e test taps at edge coordinates |

## Post-Conditions

After merge:
- The PWA is installable on Android and iOS.
- Closed-beta users can use the app offline for at least the most recent
  chapter.
- The PO has a stream of client errors visible in Fly logs.
- Lighthouse CI gate is green on every PR.
- Module 011 (push) can plug into the Notifications opt-in already wired in
  Settings.
