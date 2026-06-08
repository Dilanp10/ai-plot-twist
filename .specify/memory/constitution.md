# AI Plot Twist — Project Constitution

**Version:** 1.0.0
**Ratified:** 2026-06-07
**Status:** active

This document is the **non-negotiable contract** for every feature spec, plan, and PR in
this repository. Every Spec Kit `plan.md` must include a "Constitution Check" section that
walks through these gates and either confirms compliance or documents an explicit waiver
approved by the Product Owner.

---

## Gate 1 — Zero-Cost Discipline (during closed beta)

- [ ] Does the plan introduce any paid service, SaaS subscription, or cloud resource
      outside the documented free tiers (Fly.io, Neon, Cloudflare Pages/R2/DNS,
      GitHub Actions, Gemini Free Tier, GitHub Models, Pollinations, HuggingFace IF)?
- [ ] If a paid alternative is required for production-readiness, is it gated behind a
      feature flag that defaults OFF in the closed beta?

**Rationale**: G-1 of the SDD requires USD 0/month during the closed beta. Any drift here
invalidates the prototype's premise.

## Gate 2 — Idempotency Everywhere

- [ ] Are all state-mutating endpoints idempotent — either naturally or via
      `Idempotency-Key` headers persisted in `idempotency_keys`?
- [ ] Are all FSM transitions wrapped in `pg_advisory_xact_lock` and protected by
      `state_transitions.trigger_id UNIQUE`?
- [ ] Can every cron-triggered job be re-fired with the same `trigger_id` without
      producing duplicate side-effects?

**Rationale**: GitHub Actions cron has jitter and retries; Fly.io free tier may restart
VMs. The system MUST tolerate replays.

## Gate 3 — Timezone Anchoring

- [ ] Are all in-code time computations using `zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")`
      or stored as `TIMESTAMPTZ`?
- [ ] Are all cron schedules in GitHub Actions documented with both UTC and ART times?
- [ ] Does the plan avoid `datetime.utcnow()` (deprecated, naive) in favor of
      `datetime.now(tz=...)` (aware)?

**Rationale**: The product is a 24-hour clock. A 1-hour TZ bug ruins the loop.

## Gate 4 — Provider Abstraction for External AI

- [ ] If the feature consumes an LLM, does it go through `LLMProvider` (not a direct
      Gemini/GH Models SDK call from business logic)?
- [ ] If the feature consumes T2I, does it go through `ImageProvider` /
      `ImageProviderRouter` (never a direct `httpx` call to Pollinations from
      `generation_pipeline`)?
- [ ] Are all provider-specific concerns (rate limits, payload shapes, retries)
      encapsulated inside the provider implementation?

**Rationale**: SDD Apéndice A, decisión #14 (OQ-3). Future GPU migration must be a
config swap, not a refactor.

## Gate 5 — Determinism in Critical Paths

- [ ] Are winner-selection queries deterministic on ties
      (`ORDER BY votes DESC, submitted_at ASC, id ASC`)?
- [ ] Are LLM calls in the Director's Filter and Scriptwriter using `temperature ≤ 0.3`
      and JSON-mode with a strict `response_schema`?
- [ ] Are T2I seeds derived from stable hashes (`hash(chapter_id, panel_idx)`)
      rather than `random()`?

**Rationale**: Replays must produce the same result. Audits and bug repros depend on it.

## Gate 6 — Spanish UI, English Code

- [ ] Are all user-facing strings in **español rioplatense**?
- [ ] Are all identifiers (file names, classes, functions, variables, DB columns) in
      **English**?
- [ ] Is every new domain term added to `docs/glossary.md` before being used in code?

**Rationale**: Reduces cognitive load for non-Spanish contributors and keeps i18n a clean
swap if the product ever expands.

## Gate 7 — Soft Delete on User Content

- [ ] Are all user-authored entities (twists, votes, push subscriptions) deleted by
      flag/status (`deleted_by_user`, `deleted_at`) rather than `DELETE FROM`?
- [ ] Is the soft-delete state explicitly excluded from public reads?

**Rationale**: Audit trail + abuse investigation + accidental-delete recovery during
closed beta.

## Gate 8 — Tests from Day One

- [ ] Does the plan ship at least one unit test per new domain rule (e.g., FSM
      transition legality, tiebreak rule, quota arithmetic)?
- [ ] Does the plan ship at least one integration test per new API endpoint
      (round-trip against a real ephemeral Postgres)?
- [ ] Is the test suite added to the CI pipeline in the same PR that ships the feature?

**Rationale**: This codebase has one solo maintainer in MVP. Tests are the only
documentation that doesn't drift.

## Gate 9 — Trust Boundaries

- [ ] Are all `/internal/*` endpoints authenticated via HMAC (`X-Tick-Signature`) and
      replay-protected (`ts ± 300 s`)?
- [ ] Are all user-authenticated endpoints behind JWT validation middleware?
- [ ] Are LLM outputs treated as untrusted input (re-validated against Pydantic
      schemas, never `eval`'d, never echoed verbatim to the prompt of another LLM call
      without sanitization)?

**Rationale**: Prompt injection and tick spoofing are real risks even in a small group.

## Gate 10 — Observability Minimum

- [ ] Does every new background task emit a structured log entry with
      `cycle_id`, `chapter_id`, `outcome`, and `duration_ms`?
- [ ] Are LLM and T2I provider attempts logged with `provider`, `model`, `attempt`,
      `outcome`, `latency_ms`?
- [ ] Is the `GET /internal/health/cycle` endpoint updated to expose the new feature's
      health signal?

**Rationale**: There is no dedicated SRE. Logs are the only forensic tool.

---

## Amendment Process

1. Open an ADR (`docs/adr/NNNN-title.md`) proposing the amendment.
2. Reference the gate to add/modify/remove and the rationale.
3. PO approval is required.
4. Bump `Version` in this file using semver:
   - **MAJOR** = remove or weaken a gate.
   - **MINOR** = add a new gate.
   - **PATCH** = clarify wording without behavioral change.
5. Update the `Ratified` date.
