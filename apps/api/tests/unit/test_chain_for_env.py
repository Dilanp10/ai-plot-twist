"""Unit tests: chain_for_env factory.

Module 009 / Task T-007.
"""

from __future__ import annotations

import pytest

from app.providers.image import chain_for_env
from app.providers.image.fake import FakeImageProvider
from app.providers.image.huggingface import HuggingFaceProvider
from app.providers.image.pollinations import PollinationsProvider


def test_dev_returns_fake_only() -> None:
    chain = chain_for_env("dev")
    assert len(chain) == 1
    assert isinstance(chain[0], FakeImageProvider)


def test_mvp_returns_pollinations_then_hf() -> None:
    chain = chain_for_env("mvp", huggingface_token="hf_secret")
    assert len(chain) == 2
    assert isinstance(chain[0], PollinationsProvider)
    assert isinstance(chain[1], HuggingFaceProvider)


def test_mvp_without_token_raises() -> None:
    with pytest.raises(ValueError, match="huggingface_token"):
        chain_for_env("mvp")


def test_mvp_with_empty_token_raises() -> None:
    with pytest.raises(ValueError, match="huggingface_token"):
        chain_for_env("mvp", huggingface_token="")


def test_v02_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match=r"v02|LocalComfy|0003"):
        chain_for_env("v02")


def test_chain_names_match_provider_canonical_ids() -> None:
    """Both chains should expose providers whose .name matches the
    canonical IDs documented in SDD §4.5.2 (used by router logs / R2 paths)."""
    dev = chain_for_env("dev")
    assert [p.name for p in dev] == ["fake"]

    mvp = chain_for_env("mvp", huggingface_token="x")
    assert [p.name for p in mvp] == ["pollinations", "hf"]


def test_local_comfy_provider_construction_raises() -> None:
    """Direct instantiation must fail loudly so nobody wires it into a chain."""
    from app.providers.image.local_comfy import LocalComfyProvider

    with pytest.raises(NotImplementedError, match=r"v0\.2|reserved|0003"):
        LocalComfyProvider()
