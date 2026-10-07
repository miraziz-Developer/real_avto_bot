"""AI provayder qatlami: Gemini (video+ovoz+matn) yoki Groq. Tanlov — AI_PROVIDER (auto/gemini/groq).

Qolgan kod faqat shu paketdagi interfeysni ishlatadi: chat_json, chat, transcribe, enabled, agent_model.
"""

from __future__ import annotations

import logging
from typing import Protocol

from bot.ai.budget import AIBudget, Prices
from bot.ai.errors import AIBudgetExceeded, AIError
from bot.ai.gemini import DEFAULT_PRICES as GEMINI_PRICES
from bot.ai.gemini import GeminiClient
from bot.ai.groq import DEFAULT_PRICES as GROQ_PRICES
from bot.ai.groq import GroqClient
from bot.config import settings

logger = logging.getLogger(__name__)


class AIClient(Protocol):
    provider: str
    supports_video: bool
    model: str
    agent_model: str

    @property
    def enabled(self) -> bool: ...

    async def chat_json(self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 800) -> dict: ...

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 700,
    ) -> dict: ...

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg", mime_type: str | None = None) -> str: ...

    async def close(self) -> None: ...


_client: GeminiClient | GroqClient | None = None
_budget: AIBudget | None = None


def get_budget() -> AIBudget:
    global _budget
    if _budget is None:
        _budget = AIBudget(
            daily_usd=settings.ai_daily_budget_usd,
            user_daily_limit=settings.ai_user_daily_limit,
            redis_url=settings.redis_url,
        )
    return _budget


def _prices(default: Prices) -> Prices:
    return Prices(
        input_per_m=settings.ai_price_input_per_m or default.input_per_m,
        output_per_m=settings.ai_price_output_per_m or default.output_per_m,
        audio_per_m=settings.ai_price_audio_per_m or default.audio_per_m,
    )


def resolve_provider(provider: str, *, gemini_key: str, groq_key: str) -> str:
    p = (provider or "auto").lower()
    if p in ("gemini", "groq"):
        return p
    if p != "auto":
        logger.warning("AI_PROVIDER=%r noma'lum — auto ishlatiladi", provider)
    if gemini_key:
        return "gemini"
    return "groq"


def get_ai() -> GeminiClient | GroqClient:
    global _client
    if _client is None:
        provider = resolve_provider(
            settings.ai_provider, gemini_key=settings.gemini_api_key, groq_key=settings.groq_api_key
        )
        if provider == "gemini":
            _client = GeminiClient(
                settings.gemini_api_key,
                model=settings.gemini_model,
                agent_model=settings.gemini_agent_model,
                thinking_level=settings.gemini_thinking_level,
                media_resolution=settings.gemini_media_resolution,
                budget=get_budget(),
                prices=_prices(GEMINI_PRICES),
            )
        else:
            _client = GroqClient(
                settings.groq_api_key,
                model=settings.groq_model,
                stt_model=settings.groq_stt_model,
                agent_model=settings.agent_model,
                budget=get_budget(),
                prices=_prices(GROQ_PRICES),
            )
        logger.info(
            "AI: %s (model=%s, yoqilgan=%s, kunlik chegara=$%.2f)",
            _client.provider,
            _client.model,
            _client.enabled,
            settings.ai_daily_budget_usd,
        )
    return _client


async def close_ai() -> None:
    if _client is not None:
        await _client.close()
    if _budget is not None:
        await _budget.close()


__all__ = [
    "AIBudget",
    "AIBudgetExceeded",
    "AIClient",
    "AIError",
    "GeminiClient",
    "GroqClient",
    "close_ai",
    "get_ai",
    "get_budget",
    "resolve_provider",
]
