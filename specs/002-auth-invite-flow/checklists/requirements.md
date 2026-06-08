# Requirements Checklist: Auth via Invite Code

**Branch**: `002-auth-invite-flow` | **Date**: 2026-06-07

A PR closing module 002 is **mergeable** only when every box below is ticked. The
reviewer goes through this file linearly, ticking as evidence is provided in the PR
description (logs, test runs, screenshots).

---

## Functional Requirements

- [ ] **FR-001** — `POST /auth/redeem-invite` creates `users` row + marks `invites`
      redeemed atomically. Idempotency-Key replay returns 200 with cached body.
      Integration test `test_auth_redeem.py::test_happy_path` and
      `::test_idempotent_replay` cover both paths.
- [ ] **FR-002** — `POST /auth/refresh` returns a new JWT for a valid
      `device_secret`. Constant-time comparison verified by timing test
      (`test_auth_refresh.py::test_timing_within_5ms_p50`).
- [ ] **FR-003** — `GET /auth/me` returns user record AND updates `last_seen_at`.
      Tested with two consecutive calls; second call shows later `last_seen_at`.
- [ ] **FR-004** — JWT structure (HS256, claims `sub/iss/aud/iat/exp/jti`) verified
      by `test_jwt_service.py`. `exp = iat + 90 d` (±5 s for clock).
- [ ] **FR-005** — JWT middleware is dependency-based, not global. Public endpoints
      (`/healthz`, `/auth/redeem-invite`, `/auth/refresh`) verified to NOT require
      auth. Banned user → 403 with `code: banned`.
- [ ] **FR-006** — Code check-digit algorithm verified by `test_invite_code.py`:
      generate 10 codes, mutate the last char of each → all rejected; mutate any
      of the first 7 → check digit no longer matches.
- [ ] **FR-007** — `pnpm issue-invite` honors `--count`, `--ttl-days`, `--note`,
      `--display-name-hint`. Refuses to run with `ENV=prod` unless `--allow-prod`.
      Codes printed to stdout match DB rows. Tested in
      `test_issue_invite_cli.py`.
- [ ] **FR-008** — `pnpm revoke-invite` flips status only for `unused` invites;
      other states → clear error. `pnpm list-invites` prints a table with the
      expected columns.
- [ ] **FR-009** — Rate-limit: 5 attempts/hour per IP enforced before any DB
      lookup of the invite. `test_rate_limit.py::test_429_on_6th_attempt`.
- [ ] **FR-010** — `display_name` NFKC-normalized + control chars stripped + length
      validated. `test_display_name_normalization.py` covers Unicode, RTL
      overrides, control chars, edge lengths.
- [ ] **FR-011** — Log scrubbing verified: `pytest --log-cli-level=DEBUG` shows
      logs that do not contain the full code or device_secret. Only first 4 chars
      of code in logs.
- [ ] **FR-012** — `users.device_token` = SHA-256 hex of the raw secret.
      `hmac.compare_digest` used in refresh. Verified by code review +
      `test_auth_refresh.py::test_constant_time_comparison`.
- [ ] **FR-013** — PWA persists `jwt` + `device_secret` in IndexedDB. PWA fetch
      interceptor handles one `/auth/refresh` on 401, replays original request.
      `auth-store.test.ts` and `persistence.test.ts` cover both.
- [ ] **FR-014** — `/onboarding` screen renders, accepts code + display_name,
      auto-formats code as `XXXX-XXXX`, redirects to `/today` on success.
      Manual screenshot in PR.
- [ ] **FR-015** — Structured log `auth_check {user_public_id, decision,
      latency_ms}` emitted on every authenticated request. Verified by grep
      against test run.

## Non-Functional Requirements

- [ ] **NFR-001** — `/auth/redeem-invite` p95 < 300 ms (local k6 attached).
- [ ] **NFR-002** — `/auth/refresh` p95 < 150 ms.
- [ ] **NFR-003** — `/auth/me` p95 < 100 ms.
- [ ] **NFR-004** — JWT middleware p95 < 20 ms (cache hit path).
- [ ] **NFR-005** — Timing attack resistance: difference between non-existent
      code path and redeemed code path is within ±5 ms at p50.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — No paid services introduced. `pyjwt` and
      `python-ulid` are MIT-licensed PyPI packages.
- [ ] **Gate 2 — Idempotency** — `Idempotency-Key` honored on
      `/auth/redeem-invite`; refresh is naturally idempotent.
- [ ] **Gate 3 — TZ anchoring** — `TIMESTAMPTZ` everywhere; `exp/iat/nbf` in UTC
      seconds; human-facing logs in ART. `grep -rn 'utcnow'` empty.
- [ ] **Gate 4 — Provider abstraction** — N/A.
- [ ] **Gate 5 — Determinism** — Check-digit derivation is deterministic;
      `test_invite_code.py::test_deterministic_check_digit`.
- [ ] **Gate 6 — Spanish UI / English code** — All identifiers English;
      onboarding strings Spanish. Glossary updated (`invite`, `device_secret`).
- [ ] **Gate 7 — Soft delete** — Users use `is_banned`; invites use `status`.
      No `DELETE FROM` of user-owned data.
- [ ] **Gate 8 — Tests from day one** — Unit + integration + PWA tests listed
      above all land in this PR.
- [ ] **Gate 9 — Trust boundaries** — JWT middleware verifies signature, iss,
      aud, exp with 60 s leeway. Rate limit before DB lookup. 404 collapses
      unknown/redeemed/revoked/expired. `hmac.compare_digest` for secrets.
- [ ] **Gate 10 — Observability** — `auth_check` log emitted; CLI scripts
      append to `apps/api/var/audit_log.jsonl`.

## Documentation

- [ ] `specs/002-auth-invite-flow/quickstart.md` walked through end-to-end on a
      clean dev box; commands match output.
- [ ] `specs/README.md` module table marks `002-auth-invite-flow` as `done`
      and `003-cycle-fsm` as `in-progress`.
- [ ] No dangling `TODO` / `FIXME` in shipped code without tracking issue link.

## Sign-off

- [ ] Reviewer 1 (engineering) — name, date.
- [ ] Reviewer 2 (Product Owner) — name, date.
- [ ] Constitution amendment required? If yes, link to ADR.
