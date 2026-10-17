"""Agent javobi — kanalga bog'liq emas (Telegram bot, Business chat, Instagram Direct bir xil ishlatadi)."""

from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardMarkup

from bot.agent.fallback import fallback_reply
from bot.agent.runner import run_agent
from bot.agent.tools import AgentContext
from bot.ai import AIError, get_ai, get_budget

logger = logging.getLogger(__name__)


async def generate_agent_reply(ctx: AgentContext, text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """AI bo'lsa — to'liq agent; AI yo'q yoki xato bo'lsa — bazadan oddiy javob (fallback)."""
    ai = get_ai()
    if ai.enabled:
        # Bitta mijoz kuniga cheksiz AI javob ololmaydi (spam/xarajatdan himoya) — keyin bazadan oddiy javob
        if not await get_budget().allow_user(f"lead:{ctx.lead.id}"):
            logger.info("Lead #%s kunlik AI limitiga yetdi — oddiy javob", ctx.lead.id)
            return await fallback_reply(ctx, text)
        try:
            return await run_agent(ctx, ai, model=ai.agent_model), None
        except AIError as e:
            logger.warning("AI agent ishlamadi (lead #%s), oddiy javob: %s", ctx.lead.id, e)
    return await fallback_reply(ctx, text)
