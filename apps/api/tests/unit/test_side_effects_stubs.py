"""Unit tests: side_effects DI registry + module-003 stubs.

Module 003 / Task T-006.

All tests are pure — no DB, no network.
"""

from __future__ import annotations

import pytest

from app.domain import side_effects as se
from app.domain.side_effects import (
    director_filter_stub,
    generation_pipeline_stub,
    get,
    register,
)

# ---------------------------------------------------------------------------
# Registry correctness
# ---------------------------------------------------------------------------


class TestRegistry:
    def setup_method(self) -> None:
        """Snapshot and restore registry around each test."""
        self._original: dict[str, se.SideEffect] = dict(se._registry)

    def teardown_method(self) -> None:
        se._registry.clear()
        se._registry.update(self._original)

    def test_stubs_are_registered_at_import_time(self) -> None:
        assert "director_filter" in se._registry
        assert "generation_pipeline" in se._registry

    def test_get_returns_registered_stub(self) -> None:
        fn = get("director_filter")
        assert fn is director_filter_stub

    def test_get_generation_pipeline_stub(self) -> None:
        fn = get("generation_pipeline")
        assert fn is generation_pipeline_stub

    def test_register_then_get(self) -> None:
        async def custom_effect(chapter_id: int) -> None:
            pass

        register("custom_test", custom_effect)
        assert get("custom_test") is custom_effect

    def test_register_overwrites_existing(self) -> None:
        """Calling register() again replaces the previous implementation."""
        async def real_impl(chapter_id: int) -> None:
            pass

        register("director_filter", real_impl)
        assert get("director_filter") is real_impl

    def test_get_unknown_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError) as exc_info:
            get("nonexistent_effect")
        assert "nonexistent_effect" in str(exc_info.value)

    def test_key_error_message_lists_registered_names(self) -> None:
        with pytest.raises(KeyError) as exc_info:
            get("missing_effect")
        msg = str(exc_info.value)
        # The error should mention what IS registered.
        assert "director_filter" in msg or "Registered" in msg

    def test_multiple_effects_coexist(self) -> None:
        async def fx_a(chapter_id: int) -> None:
            pass

        async def fx_b(chapter_id: int) -> None:
            pass

        register("fx_a", fx_a)
        register("fx_b", fx_b)
        assert get("fx_a") is fx_a
        assert get("fx_b") is fx_b


# ---------------------------------------------------------------------------
# Stub behaviour
# ---------------------------------------------------------------------------


class TestDirectorFilterStub:
    @pytest.mark.anyio
    async def test_no_op_does_not_raise(self) -> None:
        await director_filter_stub(chapter_id=42)

    @pytest.mark.anyio
    async def test_accepts_any_chapter_id(self) -> None:
        for chapter_id in [1, 0, 999_999]:
            await director_filter_stub(chapter_id=chapter_id)

    @pytest.mark.anyio
    async def test_awaitable_completes(self) -> None:
        """Awaiting the stub completes without returning a value (None)."""
        await director_filter_stub(chapter_id=1)


class TestGenerationPipelineStub:
    @pytest.mark.anyio
    async def test_no_op_does_not_raise(self) -> None:
        await generation_pipeline_stub(chapter_id=7)

    @pytest.mark.anyio
    async def test_accepts_any_chapter_id(self) -> None:
        for chapter_id in [1, 0, 999_999]:
            await generation_pipeline_stub(chapter_id=chapter_id)

    @pytest.mark.anyio
    async def test_awaitable_completes(self) -> None:
        """Awaiting the stub completes without returning a value (None)."""
        await generation_pipeline_stub(chapter_id=1)
