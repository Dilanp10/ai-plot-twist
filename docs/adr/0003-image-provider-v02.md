# ADR-0003 — Image provider chain for v0.2 (LocalComfy reserved)

- **Status**: accepted
- **Date**: 2026-06-14
- **Module**: 009 (image-providers) — sets the floor; module 008 (generation-pipeline) consumes the chain
- **Author**: Dilan Perea

## Context

Module 009 ships a typed `ImageProvider` ABC + Pollinations + HuggingFace +
Fake implementations, an `ImageProviderRouter` for fallback, and a
`chain_for_env(env)` factory. The factory recognizes three environments:

- `dev` → `[FakeImageProvider]`
- `mvp` → `[PollinationsProvider, HuggingFaceProvider]`
- `v02` → reserved

The `v02` chain is supposed to prepend a `LocalComfyProvider` — a GPU-backed
ComfyUI workflow exposed over a Cloudflare Tunnel — so panel generation can
push higher-fidelity output without paying API costs.

The question this ADR closes: do we implement `LocalComfyProvider` in MVP,
or stub it for v0.2?

## Decision

We **defer** `LocalComfyProvider` to v0.2. The MVP ships only Pollinations
+ HuggingFace.

The class exists in `apps/api/app/providers/image/local_comfy.py`; its
constructor raises `NotImplementedError` immediately, and
`chain_for_env("v02")` raises a matching `NotImplementedError` rather than
returning a chain.

## Rationale

- **No GPU infrastructure today.** Implementing the provider blocks on a
  stable home-GPU rig + a long-lived Cloudflare Tunnel. The PO has neither
  set up yet, and standing them up is its own multi-day work.
- **Risk is contained.** The router + chain factory already enforce that
  consumers do not name providers directly. When LocalComfy lands, it
  plugs into the `v02` chain as the first entry; nothing in module 008
  changes.
- **MVP fidelity is acceptable.** Pollinations + HuggingFace FLUX.1-schnell
  produce 1024×1024 panels that are good enough for closed-beta storytelling.
  Higher fidelity is a "nice to have," not a release blocker.
- **Cost is bounded.** Both MVP providers have free tiers that comfortably
  cover ~30 users × 1 chapter/day. Switching to LocalComfy is a cost-and-
  latency optimization, not a correctness fix.

## Consequences

- The MVP image chain has exactly two providers and no GPU dependency.
- `chain_for_env("v02")` raises rather than silently returning the MVP
  chain — a missing `LocalComfyProvider` must surface loudly so we
  remember to implement it before flipping `env` for v0.2.
- Tests assert both shapes (`dev` → 1 fake, `mvp` → pollinations+hf) AND
  that `LocalComfyProvider()` raises on construction, so a partial
  implementation cannot accidentally land without flipping this ADR.

## Implementation milestones (v0.2)

1. Stand up the home-GPU rig (CUDA + ComfyUI + a published workflow).
2. Provision the Cloudflare Tunnel + a stable hostname.
3. Implement `LocalComfyProvider.generate` against the ComfyUI REST API
   (`POST /prompt`, poll `/history/{prompt_id}`, fetch the rendered image).
4. Implement `LocalComfyProvider.health` against the `/` endpoint with a
   2 s cap.
5. Update `chain_for_env("v02")` to return
   `[LocalComfyProvider, PollinationsProvider, HuggingFaceProvider]` so
   the GPU is preferred but graceful degradation still works.
6. Add a live smoke test gated on `LOCAL_COMFY_URL` being set.
7. Flip this ADR's status to **superseded by ADR-XXXX** once v0.2 ships.
