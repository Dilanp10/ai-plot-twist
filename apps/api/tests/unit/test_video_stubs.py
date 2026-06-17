"""Unit tests: paid T2V provider stubs (KlingProvider, RunwayProvider, LumaProvider).

Module 012 / Task T-008.

The only contract: each stub is importable, satisfies the VideoProvider ABC,
and raises NotImplementedError on every method — so the router propagates it
immediately as a misconfiguration signal.
"""

from __future__ import annotations

import pytest

from app.providers.video.base import VideoProvider, VideoRequest
from app.providers.video.stubs import KlingProvider, LumaProvider, RunwayProvider

_REQ = VideoRequest(prompt="x", seed=0)

_STUBS: list[type[VideoProvider]] = [KlingProvider, RunwayProvider, LumaProvider]


@pytest.mark.parametrize("cls", _STUBS)
def test_stub_has_name(cls: type[VideoProvider]) -> None:
    assert isinstance(cls.name, str)
    assert cls.name  # non-empty


@pytest.mark.parametrize("cls", _STUBS)
async def test_stub_health_raises_not_implemented(cls: type[VideoProvider]) -> None:
    p = cls()
    with pytest.raises(NotImplementedError):
        await p.health()


@pytest.mark.parametrize("cls", _STUBS)
async def test_stub_generate_raises_not_implemented(cls: type[VideoProvider]) -> None:
    p = cls()
    with pytest.raises(NotImplementedError):
        await p.generate(_REQ)


@pytest.mark.parametrize("cls", _STUBS)
def test_stub_capabilities_raises_not_implemented(cls: type[VideoProvider]) -> None:
    p = cls()
    with pytest.raises(NotImplementedError):
        _ = p.capabilities


def test_stub_names_are_distinct() -> None:
    names = {KlingProvider.name, RunwayProvider.name, LumaProvider.name}
    assert len(names) == 3
