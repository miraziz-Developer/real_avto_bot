"""Agent javobi — kanalga bog'liq emas (Telegram bot, Business chat, Instagram Direct bir xil ishlatadi)."""

from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardMarkup

from bot.agent.fallback import fallback_reply
from bot.agent.runner import run_agent
from bot.agent.tools import AgentContext
from bot.ai import AIError, get_ai
from bot.config import settings

logger = logging.getLogger(__name__)


async def generate_agent_reply(ctx: AgentContext, text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """AI bo'lsa — to'liq agent; AI yo'q yoki xato bo'lsa — bazadan oddiy javob (fallback)."""
    ai = get_ai()
    if ai.enabled:
        try:
            return await run_agent(ctx, ai, model=settings.agent_model), None
        except AIError as e:
            logger.warning("AI agent ishlamadi (lead #%s), oddiy javob: %s", ctx.lead.id, e)
    return await fallback_reply(ctx, text)
