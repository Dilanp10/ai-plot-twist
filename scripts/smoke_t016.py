"""T-016 smoke E2E against prod for module 005 (twists submission).

Run:
    JWT=...
    python scripts/smoke_t016.py

8 asserts per the plan:
  1. POST submit #1 ->> 201
  2. POST same IDEM + same body ->> 200, same public_id
  3. POST same IDEM + different body ->> 409 idempotency_conflict
  4. POST IDEM-B + IDEM-C (twists 2 and 3) ->> 201 × 2
  5. POST IDEM-D (4th twist, over quota) ->> 409 over_quota
  6. GET /me/twists ->> 3 items, quota.remaining=0
  7. DELETE twist 3, then DELETE again ->> 200 × 2 (idempotent)
  8. POST IDEM-E after delete ->> 409 over_quota (quota not freed)
"""

from __future__ import annotations

import os
import sys
import uuid

import urllib.request
import urllib.error
import json


API = "https://ai-plot-twist.fly.dev"
CHAPTER_ID = "216f87b5-6457-439f-8832-a9df7bba6b1e"

JWT = os.environ.get("JWT")
if not JWT:
    print("ERROR: set JWT env var", file=sys.stderr)
    sys.exit(2)


def call(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    idem: str | None = None,
) -> tuple[int, dict | None]:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {JWT}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if idem is not None:
        req.add_header("Idempotency-Key", idem)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode()
            return resp.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            parsed = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            parsed = {"raw": payload}
        return e.code, parsed


passed = 0
failed: list[str] = []


def expect(name: str, actual: int, want: int) -> bool:
    global passed
    if actual == want:
        print(f"  [OK] {name}: HTTP {actual}")
        passed += 1
        return True
    print(f"  [FAIL] {name}: HTTP {actual} (expected {want})")
    failed.append(name)
    return False


idem_a = str(uuid.uuid4())
content_a = (
    "Valentina descubre que la senal en su feed viene de su yo del futuro."
)

print("=== STEP 1: POST submit #1 (expect 201) ===")
code, body = call(
    "POST",
    "/api/v1/twists/submit",
    body={"chapter_id": CHAPTER_ID, "content": content_a},
    idem=idem_a,
)
expect("step1.submit", code, 201)
twist1_id = body["twist"]["public_id"] if code == 201 else None
remaining_after_1 = body["remaining_submissions"] if code == 201 else None
print(
    f"  ->> public_id={twist1_id}, remaining_submissions={remaining_after_1}"
)

print("\n=== STEP 2: POST same IDEM + same body (expect 200, same id) ===")
code, body = call(
    "POST",
    "/api/v1/twists/submit",
    body={"chapter_id": CHAPTER_ID, "content": content_a},
    idem=idem_a,
)
expect("step2.replay", code, 200)
if code == 200:
    same = body["twist"]["public_id"] == twist1_id
    print(f"  ->> same public_id: {'[OK]' if same else '[FAIL]'}")
    if not same:
        failed.append("step2.public_id_mismatch")

print("\n=== STEP 3: POST same IDEM + DIFFERENT body (expect 409) ===")
code, body = call(
    "POST",
    "/api/v1/twists/submit",
    body={
        "chapter_id": CHAPTER_ID,
        "content": "Otro contenido distinto y suficientemente largo.",
    },
    idem=idem_a,
)
expect("step3.idempotency_conflict", code, 409)
print(f"  ->> body={body}")

print("\n=== STEP 4: POST 2 more submits (expect 201 x 2) ===")
content_b = "Otra propuesta razonable para que el cliffhanger se resuelva."
content_c = "Tercera idea coherente con la trama del capitulo uno."
code, body = call(
    "POST",
    "/api/v1/twists/submit",
    body={"chapter_id": CHAPTER_ID, "content": content_b},
    idem=str(uuid.uuid4()),
)
expect("step4.submit_2", code, 201)
twist2_id = body["twist"]["public_id"] if code == 201 else None

code, body = call(
    "POST",
    "/api/v1/twists/submit",
    body={"chapter_id": CHAPTER_ID, "content": content_c},
    idem=str(uuid.uuid4()),
)
expect("step4.submit_3", code, 201)
twist3_id = body["twist"]["public_id"] if code == 201 else None
remaining_after_3 = body["remaining_submissions"] if code == 201 else None
print(
    f"  ->> twist3.public_id={twist3_id}, "
    f"remaining_submissions={remaining_after_3}"
)

print("\n=== STEP 5: POST 4th submit (expect 409 over_quota) ===")
code, body = call(
    "POST",
    "/api/v1/twists/submit",
    body={
        "chapter_id": CHAPTER_ID,
        "content": "Cuarta idea que deberia ser rechazada por la quota.",
    },
    idem=str(uuid.uuid4()),
)
expect("step5.over_quota", code, 409)
print(f"  ->> body={body}")

print("\n=== STEP 6: GET /me/twists (expect 200, 3 items) ===")
code, body = call("GET", "/api/v1/me/twists")
expect("step6.list_me", code, 200)
if code == 200:
    items = body.get("items", [])
    quota = body.get("quota")
    print(f"  ->> items={len(items)}, quota={quota}")
    if len(items) != 3:
        failed.append("step6.item_count")
    if quota and quota.get("remaining") != 0:
        failed.append("step6.quota_remaining")

print("\n=== STEP 7: DELETE twist 3 twice (expect 200 x 2, idempotent) ===")
code, body = call("DELETE", f"/api/v1/twists/{twist3_id}")
expect("step7.delete_first", code, 200)
deleted_at_1 = body.get("deleted_at") if body else None

code, body = call("DELETE", f"/api/v1/twists/{twist3_id}")
expect("step7.delete_second", code, 200)
deleted_at_2 = body.get("deleted_at") if body else None
if deleted_at_1 != deleted_at_2:
    print(f"  [FAIL] deleted_at changed between calls ({deleted_at_1} != {deleted_at_2})")
    failed.append("step7.deleted_at_changed")
else:
    print(f"  [OK] deleted_at stable: {deleted_at_1}")

print("\n=== STEP 8: POST after DELETE (expect 409 over_quota) ===")
code, body = call(
    "POST",
    "/api/v1/twists/submit",
    body={
        "chapter_id": CHAPTER_ID,
        "content": "Idea que NO deberia entrar despues del DELETE.",
    },
    idem=str(uuid.uuid4()),
)
expect("step8.over_quota_after_delete", code, 409)
print(f"  ->> body={body}")

print(f"\n=== RESULT: {passed}/8 passed, {len(failed)} failed ===")
if failed:
    print("FAILED:")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
print("ALL SMOKE ASSERTS PASSED.")
