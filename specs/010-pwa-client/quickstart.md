# Quickstart: PWA Polish

**Branch**: `010-pwa-client` | **Date**: 2026-06-07
**Depends on**: modules 002 + 004 + 005 + 007 merged. API + dev DB running.

---

## 1. Run the PWA in dev with the polished shell

```sh
pnpm dev
# api on :8000, web on :5173
```

Open `http://localhost:5173`. Expected:

- App shell renders within 500 ms.
- Top bar shows the cycle state badge (RECEPCION_IDEAS / VOTACION / etc.).
- Bottom nav has 4 tabs: Hoy / Votar / Mis ideas / Yo.
- Active tab matches the current cycle state per FR-002.

---

## 2. Install on Chromium

Open Chrome DevTools → Application → Manifest. Verify "Installable" verdict.

- Visit twice (Chrome requires engagement before showing the prompt).
- An in-app card "Instalá AI Plot Twist" should appear after the second visit
  + one chapter view.
- Tap "Instalar". The app installs.

Verify the install:
- Open the app from the OS app drawer.
- Confirm it runs in standalone mode (no browser chrome).

---

## 3. Simulate iOS install flow

DevTools → device toolbar → choose iPhone 14 Pro. Hard-reload.

- Within 5 s the iOS Install bottom sheet appears with the 3-step
  instructions.
- Dismiss it. Confirm it stays dismissed for the session.
- Manually clear localStorage `apt.iosSheetDismissedAt`. Reload. The sheet
  appears again.

---

## 4. Offline test

Install the PWA (step 2). DevTools → Network → Offline.

- Hard-reload the installed PWA. The shell should render < 500 ms from the
  precache.
- The most recently cached `/chapters/today` payload should display, with an
  "Sin conexión" pill.
- Go back online. The SW background-refreshes; the page swaps to the live
  chapter without a hard reload.

---

## 5. Sign-out

Navigate to `/settings`. Tap "Cerrar sesión", confirm.

Expected:
- Modal explains the device_secret will be wiped.
- On confirm: IndexedDB.aiplottwist cleared (DevTools → Application →
  IndexedDB).
- localStorage cleared (`apt.*` keys gone).
- All SW registrations unregistered (DevTools → Application → Service Workers).
- Hard navigation to `/onboarding`.

---

## 6. Trigger an error boundary

Add a temporary throw to a panel render (e.g., in `Panel.svelte`, throw
`new Error('test')` if `panel.idx === 2`). Reload `/today`.

Expected:
- The affected sub-tree renders an "Algo salió mal en este pedacito" card.
- The rest of the panels keep rendering.
- A POST to `/api/v1/internal/client-log` fires with `event=boundary_caught`.

API log:
```
{"event":"client_log_received","client_event":"boundary_caught","client_route":"/today","client_message":"test",...}
```

Revert the throw.

---

## 7. Trigger an unhandled error

In DevTools console:

```js
Promise.reject(new Error("test rejection"));
```

Expected: another POST to `/internal/client-log` with
`event=unhandled_rejection`.

---

## 8. CSP report-only mode

Verify CSP in DevTools → Network → main HTML response:

```
Content-Security-Policy-Report-Only: default-src 'self'; ...;
  report-uri https://api.aiplottwist.example/api/v1/internal/client-log
```

Try injecting a violation:

```html
<!-- in DevTools, run: -->
const s = document.createElement('script');
s.src = 'https://evil.example/x.js';
document.body.appendChild(s);
```

Expected: console warning (not enforcement); a POST to `/internal/client-log`
with `event=csp_violation` (via the report-uri).

After 1 week, switch the header to enforcing `Content-Security-Policy:` in
`_headers`. Document the rollout date in `docs/adr/0005-csp-rollout.md`.

---

## 9. Lighthouse CI locally

```sh
pnpm dlx @lhci/cli@0.13 autorun \
  --config=.lighthouserc.json
```

`.lighthouserc.json`:
```json
{
  "ci": {
    "collect": {
      "url": ["http://localhost:5173", "http://localhost:5173/today",
              "http://localhost:5173/vote"],
      "startServerCommand": "pnpm --filter ./apps/web preview",
      "numberOfRuns": 3
    },
    "assert": {
      "assertions": {
        "categories:performance": ["error", { "minScore": 0.85 }],
        "categories:accessibility": ["error", { "minScore": 0.95 }],
        "categories:best-practices": ["error", { "minScore": 0.95 }],
        "categories:pwa": ["error", { "minScore": 0.90 }],
        "first-contentful-paint": ["warn", { "maxNumericValue": 2000 }],
        "largest-contentful-paint": ["error", { "maxNumericValue": 3500 }],
        "total-blocking-time": ["error", { "maxNumericValue": 200 }]
      }
    }
  }
}
```

---

## 10. Accessibility manual smoke

### Keyboard

- Tab through `/today` from the top: focus should visit (in order) state
  badge → countdown → panel 1 caption → panel 2 caption → … → CTA.
- All interactive elements have a visible focus ring.
- `Esc` closes any open modal.

### Screen reader

- macOS: VoiceOver (`Cmd+F5`) → Safari → app URL.
- Confirm headings are announced as such (h1 = chapter title).
- Panel images announce their narration.

### Reduced motion

- macOS: System Settings → Accessibility → Display → Reduce motion.
- Reload the app. Verify transitions ≤ 0 ms (no fades, no slide-ins).

---

## 11. Bundle audit

```sh
pnpm --filter ./apps/web build
pnpm dlx vite-bundle-visualizer apps/web/dist
```

Open the treemap. Verify:
- Main entry chunk ≤ 80 KB gz.
- `vote.svelte` is in its own chunk (lazy-loaded).
- `workbox-*` is in its own chunk (precached separately).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Install prompt doesn't appear | Chrome heuristic requires engagement | Visit twice; view a chapter |
| iOS sheet shows even when already installed | UA spoofed but `navigator.standalone` returns false in DevTools | Test on real device |
| SW doesn't update | Hard-coded `skipWaiting` not set | Verify `vite-plugin-pwa` config |
| Lighthouse perf fails on first run | Cold backend (Fly machine sleeping) | Warm up before measuring; or use the production URL |
| CSP blocks a legitimate request | Report-only mode hasn't caught it yet | Add to allowlist before flipping to enforce |
| Client-log spam after deploy | A real bug | Grep `client_log_received` and triage |
