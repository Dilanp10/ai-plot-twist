"""Tests — Module 014 T-005: send_generation_email."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.email.resend_client import _build_email_html, send_generation_email


# ---------------------------------------------------------------------------
# Unit — _build_email_html
# ---------------------------------------------------------------------------


def test_build_email_html_with_winner() -> None:
    winner = {
        "twist_text": "Messi desafía a CR7",
        "author_display_name": "Dilan",
        "vote_count": "7",
        "character_name": "Lionel Messi",
        "character_slug": "messi",
        "character_photo_url": "https://cdn.example.com/static/characters/messi.webp",
    }
    html = _build_email_html(winner, "https://example.com/admin")
    assert "Messi desafía a CR7" in html
    assert "Dilan" in html
    assert "Lionel Messi" in html
    assert "https://cdn.example.com/static/characters/messi.webp" in html
    assert "https://example.com/admin" in html


def test_build_email_html_no_winner() -> None:
    html = _build_email_html(None, "https://example.com/admin")
    assert "No hay ideas aprobadas" in html
    assert "https://example.com/admin" in html


# ---------------------------------------------------------------------------
# Unit — send_generation_email: missing config → skip silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_no_api_key() -> None:
    mock_factory = MagicMock()
    # Should return without calling Resend when key is missing
    await send_generation_email(
        session_factory=mock_factory,
        chapter_id=5,
        resend_api_key=None,
        admin_email="test@example.com",
        r2_public_base_url="https://cdn.example.com",
    )
    mock_factory.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_no_admin_email() -> None:
    mock_factory = MagicMock()
    await send_generation_email(
        session_factory=mock_factory,
        chapter_id=5,
        resend_api_key="re_test_key",
        admin_email=None,
        r2_public_base_url="https://cdn.example.com",
    )
    mock_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Unit — send_generation_email: Resend called with correct payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_calls_resend() -> None:
    mock_session = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.email.resend_client._fetch_winner_for_email",
        new=AsyncMock(
            return_value={
                "twist_text": "Messi se enfrenta a...",
                "author_display_name": "Dilan",
                "vote_count": "5",
                "character_name": "Lionel Messi",
                "character_slug": "messi",
                "character_photo_url": "https://cdn.example.com/messi.webp",
            }
        ),
    ), patch("resend.Emails.send") as mock_send:
        await send_generation_email(
            session_factory=mock_session_factory,
            chapter_id=5,
            resend_api_key="re_test_key",
            admin_email="dilan@example.com",
            r2_public_base_url="https://cdn.example.com",
        )

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args[0][0]
    assert call_kwargs["to"] == ["dilan@example.com"]
    assert "🎬" in call_kwargs["subject"]
    assert "Messi se enfrenta a..." in call_kwargs["html"]


# ---------------------------------------------------------------------------
# Unit — send_generation_email: Resend error is swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_resend_error_swallowed() -> None:
    mock_session = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.email.resend_client._fetch_winner_for_email",
        new=AsyncMock(return_value=None),
    ), patch("resend.Emails.send", side_effect=Exception("API error")):
        # Must not raise
        await send_generation_email(
            session_factory=mock_session_factory,
            chapter_id=5,
            resend_api_key="re_test_key",
            admin_email="dilan@example.com",
            r2_public_base_url="https://cdn.example.com",
        )
