"""Unit tests: chain_for_env + VideoProvider public __init__.py surface.

Module 012 / Task T-007.
"""

from __future__ import annotations

import pytest

from app.providers.video import (
    MINIMAL_MP4,
    FakeVideoProvider,
    HFVideoProvider,
    PollinationsVideoProvider,
    VideoProvider,
    VideoProviderRouter,
    chain_for_env,
)


def test_dev_chain_returns_fake_only() -> None:
    chain = chain_for_env("dev")
    assert len(chain) == 1
    assert isinstance(chain[0], FakeVideoProvider)


def test_mvp_chain_returns_hf_then_pollinations() -> None:
    chain = chain_for_env("mvp", huggingface_token="hf_secret")
    assert len(chain) == 2
    assert isinstance(chain[0], HFVideoProvider)
    assert isinstance(chain[1], PollinationsVideoProvider)


def test_mvp_chain_without_token_raises() -> None:
    with pytest.raises(ValueError, match="huggingface_token"):
        chain_for_env("mvp")


def test_mvp_chain_with_empty_token_raises() -> None:
    with pytest.raises(ValueError):
        chain_for_env("mvp", huggingface_token="")


def test_unknown_env_raises() -> None:
    with pytest.raises(ValueError, match="unknown env"):
        chain_for_env("unknown")  # type: ignore[arg-type]


def test_chain_elements_are_video_providers() -> None:
    chain = chain_for_env("dev")
    for p in chain:
        assert isinstance(p, VideoProvider)


def test_minimal_mp4_importable_from_package() -> None:
    assert len(MINIMAL_MP4) == 136


def test_router_importable_from_package() -> None:
    chain = chain_for_env("dev")
    router = VideoProviderRouter(providers=chain)
    assert router.provider_names == ("fake",)
