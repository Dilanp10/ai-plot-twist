"""Resend email client — generation notification.

Module 014 / Task T-005.

Sends a notification email to the operator (ADMIN_EMAIL) when the FSM
transitions to GENERACION, containing the winning idea + character info
so the operator can create the video and upload it via the admin panel.

Error policy: any failure in ``send_generation_email`` is logged at
WARNING level and swallowed — the FSM must not be blocked by a broken
email provider. The function always returns None.

Usage (from internal_transition.py BackgroundTask)::

    await send_generation_email(
        session_factory=session_factory,
        chapter_id=chapter_id,
        resend_api_key=settings.resend_api_key,
        admin_email=settings.admin_email,
        r2_public_base_url=settings.r2_public_base_url,
        admin_panel_url="https://ai-plot-twist-pwa.pages.dev/admin",
    )
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_WINNER_SQL = sa.text(
    "SELECT"
    "  t.content          AS twist_text,"
    "  u.display_name     AS author_display_name,"
    "  COUNT(v.id)        AS vote_count,"
    "  c.slug             AS character_slug,"
    "  c.display_name     AS character_name,"
    "  c.photo_r2_key     AS character_photo_r2_key"
    " FROM twists t"
    " JOIN users u        ON u.id = t.user_id"
    " LEFT JOIN votes v   ON v.twist_id = t.id"
    " LEFT JOIN characters c ON c.id = t.character_id"
    " WHERE t.chapter_id = :chapter_id"
    "   AND t.status = 'approved'"
    " GROUP BY t.id, t.content, u.display_name, c.slug, c.display_name, c.photo_r2_key"
    " ORDER BY COUNT(v.id) DESC, t.submitted_at ASC, t.id ASC"
    " LIMIT 1"
)


async def _fetch_winner_for_email(
    session: AsyncSession,
    chapter_id: int,
    r2_public_base_url: str,
) -> dict[str, str] | None:
    result = await session.execute(_WINNER_SQL, {"chapter_id": chapter_id})
    row = result.mappings().one_or_none()
    if row is None:
        return None
    photo_url = f"{r2_public_base_url.rstrip('/')}/{row['character_photo_r2_key']}"
    return {
        "twist_text": str(row["twist_text"]),
        "author_display_name": str(row["author_display_name"]),
        "vote_count": str(row["vote_count"]),
        "character_name": str(row["character_name"]),
        "character_slug": str(row["character_slug"]),
        "character_photo_url": photo_url,
    }


def _build_email_html(
    winner: dict[str, str] | None,
    admin_panel_url: str,
) -> str:
    if winner is None:
        body = "<p>No hay ideas aprobadas para este capítulo. Entrá al panel para revisar.</p>"
    else:
        body = f"""
        <h2>🏆 Idea ganadora</h2>
        <blockquote style="border-left:4px solid #f59e0b;padding:8px 16px;margin:16px 0;color:#374151;">
            {winner['twist_text']}
        </blockquote>
        <p><strong>Autor:</strong> {winner['author_display_name']}</p>
        <p><strong>Votos:</strong> {winner['vote_count']}</p>
        <hr>
        <h2>🎭 Personaje</h2>
        <p><strong>{winner['character_name']}</strong> ({winner['character_slug']})</p>
        <img src="{winner['character_photo_url']}" alt="{winner['character_name']}"
             style="width:200px;height:200px;object-fit:cover;border-radius:8px;">
        """

    return f"""
    <html>
    <body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#111827;">
        <h1 style="color:#dc2626;">AI Plot Twist — Video listo para generar</h1>
        {body}
        <hr>
        <p>
            <a href="{admin_panel_url}"
               style="display:inline-block;padding:12px 24px;background:#dc2626;
                      color:white;text-decoration:none;border-radius:6px;font-weight:bold;">
                Abrir panel admin → subir video
            </a>
        </p>
        <p style="color:#6b7280;font-size:12px;">
            Este email fue generado automáticamente por AI Plot Twist.
        </p>
    </body>
    </html>
    """


async def send_generation_email(
    session_factory: async_sessionmaker[AsyncSession],
    chapter_id: int,
    resend_api_key: str | None,
    admin_email: str | None,
    r2_public_base_url: str | None,
    admin_panel_url: str = "https://ai-plot-twist-pwa.pages.dev/admin",
) -> None:
    """Send the winner notification email to the operator.

    Swallows all exceptions — email failure must never block the FSM.
    """
    if not resend_api_key or not admin_email:
        logger.warning(
            "generation_email_skipped chapter_id=%d reason=missing_config "
            "resend_api_key_set=%s admin_email_set=%s",
            chapter_id,
            bool(resend_api_key),
            bool(admin_email),
        )
        return

    try:
        winner: dict[str, str] | None = None
        if r2_public_base_url:
            async with session_factory() as session:
                winner = await _fetch_winner_for_email(
                    session, chapter_id, r2_public_base_url
                )

        html = _build_email_html(winner, admin_panel_url)

        import resend as resend_sdk  # lazy import — optional dependency

        resend_sdk.api_key = resend_api_key
        resend_sdk.Emails.send(
            {
                "from": "AI Plot Twist <onboarding@resend.dev>",
                "to": [admin_email],
                "subject": "🎬 Video listo para generar — AI Plot Twist",
                "html": html,
            }
        )
        logger.info(
            "generation_email_sent chapter_id=%d to=%s", chapter_id, admin_email
        )

    except Exception as exc:
        logger.warning(
            "generation_email_failed chapter_id=%d error=%r", chapter_id, exc
        )
