"""Export the FastAPI OpenAPI schema to ``apps/api/openapi.json``.

This script is the source of truth for the *generated* OpenAPI document
shipped with the repo. Module 001 / Task T-013.

Usage::

    # From the repo root:
    uv --directory apps/api run python ../../scripts/export_openapi.py

    # (Once T-018 ships the pnpm wiring:)
    pnpm --filter ./apps/api openapi:export

CI (T-020) runs this and then asserts the committed file is unchanged via
``git diff --exit-code apps/api/openapi.json``. If you intentionally change
a route, you must re-run this script and commit the new ``openapi.json``.

Determinism::

  * ``sort_keys=True`` makes the JSON byte-stable across runs / OSes.
  * ``indent=2`` keeps diffs human-reviewable.
  * Trailing newline keeps POSIX tools (``diff``, ``cat``) happy and avoids
    "no newline at end of file" noise in editors.

Cross-check::

  Before writing, the script verifies that every entry in
  ``REQUIRED_OPERATION_IDS`` appears in the generated schema. Keeping this
  list small and in sync with ``specs/.../contracts/*.yaml`` catches the
  common bug of accidentally dropping ``operation_id=`` from a route.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "apps" / "api"
OUTPUT = API_DIR / "openapi.json"

# Ensure ``app.*`` is importable regardless of the script's cwd.
sys.path.insert(0, str(API_DIR))

# Settings requires DATABASE_URL and JWT_SECRET to load; we never connect or
# sign anything here, so any placeholder is fine. ``setdefault`` preserves a
# real value if one is already exported in the environment.
os.environ.setdefault("ENV", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://_:_@_:1/_")
os.environ.setdefault("TICK_SECRET", "_")
os.environ.setdefault("JWT_SECRET", "_")

# Operation IDs that MUST appear in the generated schema. Update when adding
# a new endpoint contract; keep in sync with ``specs/.../contracts/*.yaml``.
REQUIRED_OPERATION_IDS: frozenset[str] = frozenset(
    {
        "getHealth",  # specs/001-project-bootstrap/contracts/health.yaml
        "postInternalTransition",  # specs/001-project-bootstrap/contracts/health.yaml
        "getChaptersToday",  # specs/004-chapters-content/contracts/chapters.yaml
    }
)


def main() -> int:
    """Render the schema, validate, write the JSON. Returns the exit code."""
    # Imported here so the env-var setup above runs first.
    from app.main import create_app

    app = create_app()
    schema: dict[str, Any] = app.openapi()

    present = _collect_operation_ids(schema)
    missing = REQUIRED_OPERATION_IDS - present
    if missing:
        print(
            "ERROR: required operationIds missing from generated schema: "
            + ", ".join(sorted(missing)),
            file=sys.stderr,
        )
        print(f"       present: {sorted(present)}", file=sys.stderr)
        return 1

    serialized = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    OUTPUT.write_text(serialized, encoding="utf-8", newline="\n")

    rel = OUTPUT.relative_to(REPO_ROOT)
    print(f"Wrote {rel}  ({len(serialized):,} bytes)")
    print(f"Operation IDs present: {sorted(present)}")
    return 0


def _collect_operation_ids(schema: dict[str, Any]) -> set[str]:
    """Walk schema.paths.* and collect every ``operationId``."""
    ids: set[str] = set()
    paths = schema.get("paths") or {}
    if not isinstance(paths, dict):
        return ids
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for op in path_item.values():
            if isinstance(op, dict):
                op_id = op.get("operationId")
                if isinstance(op_id, str):
                    ids.add(op_id)
    return ids


if __name__ == "__main__":
    sys.exit(main())
