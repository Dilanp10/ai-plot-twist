"""Import-graph guard: business modules MUST NOT touch video-provider internals.

Module 012 / Task T-010.

Walks every ``.py`` file under ``app/api``, ``app/domain``, and ``app/scripts``
and asserts that:

  1. None contain the banned URL literals (``video.pollinations.ai`` or
     ``api-inference.huggingface.co``). These string constants belong
     exclusively to the provider sub-modules.
  2. None import directly from ``app.providers.video.<submodule>``.
     Consumers must import from ``app.providers.video`` (the package root)
     so the chain factory + router can be swapped at deploy time without
     code edits.

A failure here is a real-world risk — module 008's generation pipeline is
the first business consumer, and a "convenient" direct import would silently
undo the provider abstraction built in module 012.
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

_BUSINESS_DIRS: tuple[Path, ...] = (
    _APP_DIR / "api",
    _APP_DIR / "domain",
    _APP_DIR / "scripts",
)

_BANNED_LITERALS: tuple[str, ...] = (
    "video.pollinations.ai",
    "api-inference.huggingface.co",
)

_BANNED_IMPORTS: frozenset[str] = frozenset(
    {
        "app.providers.video.hf",
        "app.providers.video.pollinations",
        "app.providers.video.fake",
        "app.providers.video.router",
        "app.providers.video.base",
        "app.providers.video.paths",
        "app.providers.video.stubs",
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
    """At least one business directory must exist so the guard is not vacuous."""
    assert any(d.exists() for d in _BUSINESS_DIRS), (
        f"No business directories found under {_APP_DIR}; "
        f"the import-graph guard would vacuously pass."
    )


@pytest.mark.parametrize("path", _iter_business_py_files(), ids=lambda p: str(p))
def test_no_banned_literal_in_business_file(path: Path) -> None:
    """No business file may embed a Pollinations / HF video URL literal."""
    text = path.read_text(encoding="utf-8")
    for literal in _BANNED_LITERALS:
        assert literal not in text, (
            f"Banned literal {literal!r} found in {path.relative_to(_API_DIR)}. "
            f"URLs belong to the video provider sub-modules, not the consumers."
        )


@pytest.mark.parametrize("path", _iter_business_py_files(), ids=lambda p: str(p))
def test_no_banned_import_in_business_file(path: Path) -> None:
    """No business file may import from a video provider sub-module directly."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"Failed to parse {path}: {exc}")

    imported = _imports_in(tree)
    leaks = imported & _BANNED_IMPORTS
    assert not leaks, (
        f"{path.relative_to(_API_DIR)} imports {sorted(leaks)} directly. "
        f"Use 'from app.providers.video import ...' instead."
    )


def test_video_urls_present_in_provider_submodules() -> None:
    """Defensive: the URL literals MUST appear inside the provider sub-modules.

    If they vanished from hf.py or pollinations.py the endpoint would be
    silently broken; this test pins them in their expected home.
    """
    hf = (_APP_DIR / "providers" / "video" / "hf.py").read_text(encoding="utf-8")
    pollinations = (
        _APP_DIR / "providers" / "video" / "pollinations.py"
    ).read_text(encoding="utf-8")

    assert "api-inference.huggingface.co" in hf
    assert "video.pollinations.ai" in pollinations
