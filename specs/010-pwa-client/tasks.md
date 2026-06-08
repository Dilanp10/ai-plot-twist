# Task Breakdown: PWA Polish

**Branch**: `010-pwa-client` | **Date**: 2026-06-07

---

## Phase 0 — App shell + routing (3 PRs)

### T-001 — Strings + theme tokens [P]
**Files**:
- `apps/web/src/lib/strings.ts`
- `apps/web/src/lib/theme-tokens.css`
- `apps/web/tests/unit/strings.test.ts`

### T-002 — Route resolver → 004-merged [P]
**Files**:
- `apps/web/src/lib/route-resolver.ts`
- `apps/web/tests/unit/route-resolver.test.ts`

**API**:
```ts
export function pickRoute(cycleState: CycleState, killSwitch?: KillSwitchInfo): RouteName;
```

Tests: each cycle state → expected route; kill-switch → maintenance route.

### T-003 — `AppShell` + `BottomNav` + `TopBar` → T-001
**Files**:
- `apps/web/src/lib/components/AppShell.svelte`
- `apps/web/src/lib/components/BottomNav.svelte`
- `apps/web/src/lib/components/TopBar.svelte`
- `apps/web/src/App.svelte` (REPLACED to mount shell + router)
- `apps/web/tests/unit/app-shell.test.ts`

---

## Phase 1 — Service worker + install (3 PRs)

### T-004 — Workbox strategy finalization → T-003
**Files**:
- `apps/web/vite.config.ts` (updated `vite-plugin-pwa` strategies per FR-003)
- `apps/web/tests/e2e/offline-flow.spec.ts`

### T-005 — Android install prompt → T-004 [P]
**Files**:
- `apps/web/src/lib/install-prompt.ts`
- `apps/web/src/lib/components/InstallPromptCard.svelte`
- `apps/web/tests/unit/install-prompt.test.ts`
- `apps/web/tests/e2e/install-android.spec.ts`

### T-006 — iOS install sheet → T-005 [P]
**Files**:
- `apps/web/src/lib/ios-install-sheet.ts`
- `apps/web/src/lib/components/IosInstallSheet.svelte`
- `apps/web/tests/e2e/install-ios.spec.ts`

---

## Phase 2 — Settings + sign-out (2 PRs)

### T-007 — `Settings` route → 002-merged
**Files**:
- `apps/web/src/routes/settings.svelte`
- `apps/web/src/lib/sign-out.ts`
- `apps/web/tests/e2e/sign-out.spec.ts`

### T-008 — SW update notifier [P]
**Files**:
- `apps/web/src/lib/sw-update-notifier.ts`
- `apps/web/src/lib/components/SwUpdateToast.svelte`
- `apps/web/tests/unit/sw-update-notifier.test.ts`

---

## Phase 3 — Error boundary + skeletons + logger (3 PRs)

### T-009 — `Skeleton.svelte` + per-route skeleton screens [P]
**Files**:
- `apps/web/src/lib/components/Skeleton.svelte`
- updates to `today.svelte`, `vote.svelte`, `me.svelte` to use skeleton when
  store status='loading'.

### T-010 — `ErrorBoundary` Svelte component [P]
**Files**:
- `apps/web/src/lib/components/ErrorBoundary.svelte`
- `apps/web/tests/e2e/error-boundary.spec.ts`

### T-011 — `client-logger.ts` (throttled queue) + global handlers → T-010
**Files**:
- `apps/web/src/lib/client-logger.ts`
- `apps/web/src/main.ts` (install global handlers)
- `apps/web/tests/unit/client-logger.test.ts`

---

## Phase 4 — Backend endpoint (1 PR)

### T-012 — `POST /internal/client-log` → 002-merged (rate-limit table)
**Files**:
- `apps/api/app/api/internal_client_log.py`
- `apps/api/tests/integration/test_client_log_endpoint.py`

**Behavior**: parses payload, applies 4 KB limit (413), IP rate-limit
(429), logs `client_log_received`. Returns 202.

---

## Phase 5 — A11y + perf + CSP (3 PRs)

### T-013 — A11y pass + `axe-core` integration → T-003..T-011 [P]
**Files**:
- `apps/web/tests/e2e/a11y-keyboard.spec.ts`
- updates to components (focus rings, ARIA, semantics)
- ESLint rule: `eslint-plugin-jsx-a11y` analog for Svelte (`eslint-plugin-svelte` a11y subset)

### T-014 — Performance budget audit + lazy routes [P]
**Files**:
- `apps/web/vite.config.ts` (manual chunks; lazy import for `vote.svelte`,
  `settings.svelte`)
- `apps/web/scripts/bundle-audit.mjs` (fails CI if main route > 80 KB gz)

### T-015 — CSP `_headers` + ADR → T-012
**Files**:
- `apps/web/public/_headers`
- `docs/adr/0005-csp-rollout.md`

---

## Phase 6 — Lighthouse CI (1 PR)

### T-016 — `.github/workflows/lighthouse-ci.yml` → T-013, T-014
**Files**:
- `.github/workflows/lighthouse-ci.yml`
- `.lighthouserc.json`

**Behavior**: waits for Pages preview deploy; runs LHCI with the budget;
fails PR on miss.

---

## Phase 7 — Deploy + real-device smoke (1 PR)

### T-017 — Deploy + manual smoke → all prior
**Files**:
- `specs/010-pwa-client/quickstart.md` (verified on real Android + iOS)
- `specs/README.md` (mark 010 done; 011 in-progress)

**Done when**: the PO installs the PWA from their phone, opens it offline the
next morning, sees yesterday's chapter, signs out, and re-onboards with a new
invite — all without dev intervention.

---

## Done-when (module-level acceptance)

1. All 17 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. Lighthouse CI green on `main`.
4. Real-device smoke completed on Android + iOS.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Shell + routing | T-001..T-003 | 2 |
| 1 — SW + install | T-004..T-006 | 2 |
| 2 — Settings | T-007..T-008 | 1.5 |
| 3 — Errors + skeletons | T-009..T-011 | 2 |
| 4 — Backend endpoint | T-012 | 0.5 |
| 5 — A11y + perf + CSP | T-013..T-015 | 2.5 |
| 6 — Lighthouse CI | T-016 | 0.5 |
| 7 — Real-device smoke | T-017 | 1 |
| **Total** | 17 tasks | **≈ 12 days** |

Buffer +25% for iOS Safari surprises and Lighthouse iteration → **plan for
15 working days**.
