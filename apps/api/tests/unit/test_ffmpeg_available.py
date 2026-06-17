"""Sanity test: ffmpeg system binary is available at runtime.

Module 008 / Task T-017 delta.

The Dockerfile installs ffmpeg explicitly so the stitch pipeline never
hits ``FileNotFoundError`` mid-generation. This test guards that contract.

Locally on developer machines (where the host may not have ffmpeg
installed) the test SKIPS rather than failing — devs run unit tests
against the Python virtual env, not the Docker image. The same test
runs hard inside the prod Docker layer via ``RUN ffmpeg -version`` in
the Dockerfile, which is the actual deploy-time guarantee.
"""

from __future__ import annotations

import shutil

import pytest


def test_ffmpeg_binary_on_path() -> None:
    """``ffmpeg`` MUST be on PATH at runtime in the Docker image.

    Locally we skip when the host doesn't have ffmpeg — the Dockerfile's
    ``RUN ffmpeg -version`` is the real production guard.
    """
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.skip(
            "ffmpeg not installed on host (acceptable in dev; "
            "the Docker image installs it explicitly)."
        )
    assert binary, "ffmpeg lookup returned an empty string"


def test_ffmpeg_python_importable() -> None:
    """The ``ffmpeg-python`` wrapper used by stitch_pipeline must import."""
    import ffmpeg

    assert hasattr(ffmpeg, "input")
    assert hasattr(ffmpeg, "concat")
    assert hasattr(ffmpeg, "output")
    assert hasattr(ffmpeg, "Error")
