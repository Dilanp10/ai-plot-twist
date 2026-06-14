"""Scriptwriter — LLM-backed chapter script drafter.

Module 008 / Task T-008.

Wraps :class:`LLMProviderRouter` to produce a :class:`ScriptwriterResponse`
for each chapter. The caller is responsible for building :class:`ScriptContext`
from the DB and for handling :exc:`LLMProviderError` (which the pipeline
coordinator catches and translates to a FAILED cycle transition).

Prompt version: ``scriptwriter_v1``. Bumping to v2 requires updating
:mod:`app.domain.scriptwriter_prompts` — do NOT hot-edit v1 in production.
"""

from __future__ import annotations

import logging
from typing import cast

from app.domain.scriptwriter_prompts import (
    ScriptContext,
    load_auto_system_prompt,
    load_system_prompt,
    render_user_prompt,
)
from app.domain.scriptwriter_response import ScriptwriterResponse
from app.providers.llm.router import LLMProviderRouter

logger = logging.getLogger(__name__)

_TEMPERATURE = 0.6
_MAX_OUTPUT_TOKENS = 4096


class Scriptwriter:
    """LLM-powered scriptwriter for AI Plot Twist chapter drafting.

    Parameters
    ----------
    llm_router:
        Pre-configured router used for the ``chat_json`` call.
    """

    def __init__(self, llm_router: LLMProviderRouter) -> None:
        self._router = llm_router

    async def draft(self, context: ScriptContext) -> ScriptwriterResponse:
        """Draft a chapter script from *context*.

        Selects the auto-continue system prompt when
        ``context.winner_content is None``; otherwise uses the winner-mode
        prompt.

        Parameters
        ----------
        context:
            All inputs the scriptwriter needs: season bible, recent chapters,
            current chapter metadata, and optionally the winner twist.

        Returns
        -------
        ScriptwriterResponse
            Parsed and validated response from the LLM.

        Raises
        ------
        LLMProviderError
            When all providers in the router are exhausted. The pipeline
            coordinator must catch this and fail the cycle.
        """
        if context.winner_content is None:
            system = load_auto_system_prompt()
            mode = "auto"
        else:
            system = load_system_prompt()
            mode = "winner"

        user = render_user_prompt(context)

        logger.info(
            "scriptwriter_draft_start chapter_day=%d mode=%s",
            context.current_chapter.day_index,
            mode,
        )

        response = await self._router.chat_json(
            system=system,
            user=user,
            response_schema=ScriptwriterResponse,
            temperature=_TEMPERATURE,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )

        logger.info(
            "scriptwriter_draft_done chapter_day=%d provider=%s model=%s latency_ms=%d",
            context.current_chapter.day_index,
            response.provider,
            response.model,
            response.latency_ms,
        )

        return cast(ScriptwriterResponse, response.content)
