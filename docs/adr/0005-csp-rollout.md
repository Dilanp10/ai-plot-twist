# ADR-0005 — Content-Security-Policy rollout in two phases

- **Status**: accepted
- **Date**: 2026-06-14
- **Module**: 010 (pwa-client) — T-015
- **Author**: Dilan Perea

## Context

Spec FR-013 mandates a Content-Security-Policy on the PWA so a stray
third-party script or an asset URL injected via a stored XSS cannot
exfiltrate user data. The PWA is served from Cloudflare Pages, which
supports per-path response headers via a top-level `_headers` file in
`apps/web/public/`.

The catch: the moment we deploy an enforcing CSP, ANY missed asset URL
(a fonts.gstatic.com fallback, an inline `<style>` we forgot about) is
a hard breakage — the browser refuses to load it without warning. With
a closed beta on real devices that are hard to instrument, that's a
bad first-touch experience.

## Decision

Roll the CSP in two phases:

1. **Phase 1 (now)** — ship a `Content-Security-Policy-Report-Only`
   header. The policy is the full enforced policy from FR-013. Every
   violation is reported via `report-uri` to
   `/api/v1/internal/client-log` (T-012 — same endpoint the
   `ErrorBoundary` and global handlers use).

2. **Phase 2 (post-beta)** — when the violation feed is empty for 7
   days running, flip the header name from `Content-Security-Policy-
   Report-Only` to `Content-Security-Policy`. No body change.

The Cloudflare Pages `_headers` file lives at
`apps/web/public/_headers` and is copied verbatim by `vite build`.

## Consequences

- Phase 1 is no-risk: even a misconfigured directive only fills the
  log, never blocks an asset.
- The flip in Phase 2 is one line — review-friendly, easy to revert.
- The CSP itself follows the principle of least authority:
  - `default-src 'self'` blocks everything else by default.
  - `img-src` / `media-src` allow the R2-served assets domain only.
  - `script-src 'self'` keeps inline + external scripts off.
  - `style-src 'self' 'unsafe-inline'` is the only `unsafe-*` —
    Svelte 5 emits scoped `<style>` blocks that compile to inline
    style tags. A future task can switch to nonces if we want to drop
    `unsafe-inline`.
  - `connect-src 'self' https://api.aiplottwist.example` matches the
    two backends fetch ever talks to.
  - `frame-ancestors 'none'` blocks iframe embedding.

## Alternatives considered

- **Direct enforcing CSP (no report-only phase)**. Cheaper to ship,
  but a missed directive is an outage. Rejected — beta UX > velocity.
- **Use the `report-to` directive instead of `report-uri`**. `report-to`
  is the modern standard but Safari < 17 still ignores it. Sticking with
  the deprecated `report-uri` until Safari 17 is the floor of our
  target devices.
- **Serve the CSP via meta tag**. Works but does NOT cover the
  bootstrap script — the meta tag is parsed AFTER the document opens,
  by which point the bootstrap script has already loaded. Header
  beats meta.
