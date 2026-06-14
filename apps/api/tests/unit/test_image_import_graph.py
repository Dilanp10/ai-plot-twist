"""Import-graph guard: business modules MUST NOT touch image-provider internals.

Module 009 / Task T-008.

Walks every ``.py`` file under ``app/api``, ``app/domain``, and ``app/scripts``
and asserts that:

  1. None contain the banned URL literals (``image.pollinations.ai`` or
     ``api-inference.huggingface.co``). These string constants belong
     exclusively to the provider sub-modules.
  2. None import directly from
     ``app.providers.image.pollinations`` / ``.huggingface`` / ``.fake``
     / ``.local_comfy`` / ``.router``. Consumers must import from
     ``app.providers.image`` (the package root) so the chain factory +
     router can be swapped at deploy time without code edits.

A failure here is a real-world risk — module 008's generation pipeline
is the first business consumer, and a "convenient" direct import would
silently undo the provider abstraction.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_DIR = Path(__file__).parent.parent.parent  # apps/api/
_APP_DIR = _API_DIR / "app"

# Scanned directories: business modules that must stay provider-agnostic.
_BUSINESS_DIRS: tuple[Path, ...] = (
    _APP_DIR / "api",
    _APP_DIR / "domain",
    _APP_DIR / "scripts",
)

# Forbidden URL literals — they belong only to the provider sub-modules.
_BANNED_LITERALS: tuple[str, ...] = (
    "image.pollinations.ai",
    "api-inference.huggingface.co",
)

# Forbidden import targets — the provider sub-module FQNs.
_BANNED_IMPORTS: frozenset[str] = frozenset(
    {
        "app.providers.image.pollinations",
        "app.providers.image.huggingface",
        "app.providers.image.fake",
        "app.providers.image.local_comfy",
        "app.providers.image.router",
        "app.providers.image.base",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_business_py_files() -> list[Path]:
    files: list[Path] = []
    for base in _BUSINESS_DIRS:
        if not base.exists():
            continue
        files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _imports_in(tree: ast.AST) -> set[str]:
    """Collect every dotted module name imported anywhere in ``tree``.

    Includes both ``import X`` (X is the module) and
    ``from X import Y`` (X is the module). Relative imports are skipped
    because the business modules never use them.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.level == 0
        ):
            found.add(node.module)
    return found


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_business_dirs_exist() -> None:
    """At least one business directory must exist; sanity check the discovery."""
    assert any(d.exists() for d in _BUSINESS_DIRS), (
        f"No business directories found under {_APP_DIR}; "
        f"the import-graph guard would vacuously pass."
    )


@pytest.mark.parametrize("path", _iter_business_py_files(), ids=lambda p: str(p))
def test_no_banned_literal_in_business_file(path: Path) -> None:
    """No business file may embed a Pollinations / HF URL literal."""
    text = path.read_text(encoding="utf-8")
    for literal in _BANNED_LITERALS:
        assert literal not in text, (
            f"Banned literal {literal!r} found in {path.relative_to(_API_DIR)}. "
            f"URLs belong to the provider sub-modules, not the consumers."
        )


@pytest.mark.parametrize("path", _iter_business_py_files(), ids=lambda p: str(p))
def test_no_banned_import_in_business_file(path: Path) -> None:
    """No business file may import from a provider sub-module directly.

    Consumers must import from ``app.providers.image`` (the package root)
    so the chain + router stay swappable.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"Failed to parse {path}: {exc}")

    imported = _imports_in(tree)
    leaks = imported & _BANNED_IMPORTS
    assert not leaks, (
        f"{path.relative_to(_API_DIR)} imports {sorted(leaks)} directly. "
        f"Use 'from app.providers.image import ...' instead."
    )


def test_provider_files_are_allowed_to_use_literals() -> None:
    """Defensive: the literals MUST appear inside the provider sub-modules.

    If they vanished from app/providers/image/pollinations.py the URL
    pattern would be silently broken; this test pins them in their
    expected home so a future refactor cannot silently move them
    elsewhere without us noticing.
    """
    pollinations = (
        _APP_DIR / "providers" / "image" / "pollinations.py"
    ).read_text(encoding="utf-8")
    huggingface = (
        _APP_DIR / "providers" / "image" / "huggingface.py"
    ).read_text(encoding="utf-8")

    assert "image.pollinations.ai" in pollinations
    assert "api-inference.huggingface.co" in huggingface
