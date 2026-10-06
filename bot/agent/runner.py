"""Agent sikli: model → asbob chaqiruvlari → model ... → mijozga yakuniy javob."""

from __future__ import annotations

import logging
import re
from typing import Protocol

from bot.agent.prompt import build_system_prompt
from bot.agent.tools import TOOL_SCHEMAS, AgentContext
from bot.ai.errors import AIBudgetExceeded, AIError

logger = logging.getLogger(__name__)

MAX_STEPS = 6
FALLBACK_REPLY = "Uzr, savolingizni biroz aniqroq yozib bera olasizmi? Masalan: qaysi model va qancha byudjet."
_MD_RE = re.compile(r"(\*\*|__|^#{1,6}\s*|`)", re.MULTILINE)


class ChatModel(Protocol):
    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 700,
    ) -> dict: ...


def clean_reply(text: str) -> str:
    """Telegramga oddiy matn: markdown belgilarini olib tashlash, uzunlikni cheklash."""
    t = _MD_RE.sub("", text or "").strip()
    return t[:3500]


async def build_messages(ctx: AgentContext) -> list[dict]:
    car = await ctx.current_car()
    messages: list[dict] = [{"role": "system", "content": build_system_prompt(ctx.lead, car)}]
    for m in await ctx.leads.history(ctx.lead):
        if m.role == "user":
            messages.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            messages.append({"role": "assistant", "content": m.content})
        elif m.role == "admin":
            messages.append({"role": "assistant", "content": f"(Menejer yozgan): {m.content}"})
    return messages


async def run_agent(ctx: AgentContext, llm: ChatModel, *, model: str | None = None) -> str:
    """Mijozning oxirgi xabari (tarixda saqlangan) ga javob tayyorlash."""
    messages = await build_messages(ctx)
    for step in range(MAX_STEPS):
        try:
            resp = await llm.chat(messages, tools=TOOL_SCHEMAS, model=model)
        except AIBudgetExceeded:
            raise
        except AIError as e:
            # Model ba'zan asbob chaqiruvini buzadi (Groq: tool_use_failed) — bir marta asbobsiz javob so'raymiz,
            # shu paytgacha topilgan natijalar (tool javoblari) kontekstda qoladi
            logger.warning("Agent lead #%s, qadam %s: %s — asbobsiz qayta urinish", ctx.lead.id, step, e)
            resp = await llm.chat(messages, tools=None, model=model)
            return clean_reply(resp.get("content") or "") or FALLBACK_REPLY
        calls = resp.get("tool_calls") or []
        if not calls:
            text = clean_reply(resp.get("content") or "")
            return text or FALLBACK_REPLY
        messages.append({"role": "assistant", "content": resp.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = (call.get("function") or {}).get("name", "")
            args = (call.get("function") or {}).get("arguments")
            result = await ctx.execute(fn, args)
            logger.info("Agent lead #%s → %s(%s) = %s", ctx.lead.id, fn, args, result[:300])
            messages.append({"role": "tool", "tool_call_id": call.get("id", fn), "name": fn, "content": result})
    logger.warning("Agent lead #%s: %s qadamda yakuniy javob bo'lmadi", ctx.lead.id, MAX_STEPS)
    return FALLBACK_REPLY
