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


class ResilientAIClient:
    """Gemini asosiy provayder, agar 429 (quota), 503 (band) yoki boshqa xato bo'lsa Groq'ga avtomatik o'tadi."""

    def __init__(self, primary: GeminiClient, fallback: GroqClient | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def provider(self) -> str:
        return self.primary.provider

    @property
    def supports_video(self) -> bool:
        return self.primary.supports_video

    @property
    def model(self) -> str:
        return self.primary.model

    @property
    def agent_model(self) -> str:
        return self.primary.agent_model

    @property
    def enabled(self) -> bool:
        return self.primary.enabled or bool(self.fallback and self.fallback.enabled)

    async def chat_json(self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 800) -> dict:
        try:
            return await self.primary.chat_json(system, user, temperature=temperature, max_tokens=max_tokens)
        except (AIError, Exception) as e:
            if self.fallback and self.fallback.enabled and not isinstance(e, AIBudgetExceeded):
                logger.warning("Gemini chat_json vaqtincha ishlamadi (%s) — Groq'ga avtomatik o'tildi", e)
                return await self.fallback.chat_json(system, user, temperature=temperature, max_tokens=max_tokens)
            raise

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 700,
    ) -> dict:
        try:
            return await self.primary.chat(
                messages, tools=tools, model=model, temperature=temperature, max_tokens=max_tokens
            )
        except (AIError, Exception) as e:
            if self.fallback and self.fallback.enabled and not isinstance(e, AIBudgetExceeded):
                logger.warning("Gemini chat vaqtincha ishlamadi (%s) — Groq'ga avtomatik o'tildi", e)
                return await self.fallback.chat(
                    messages, tools=tools, model=None, temperature=temperature, max_tokens=max_tokens
                )
            raise

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg", mime_type: str | None = None) -> str:
        try:
            return await self.primary.transcribe(audio, filename=filename, mime_type=mime_type)
        except (AIError, Exception) as e:
            if self.fallback and self.fallback.enabled and not isinstance(e, AIBudgetExceeded):
                logger.warning("Gemini transcribe vaqtincha ishlamadi (%s) — Groq'ga avtomatik o'tildi", e)
                return await self.fallback.transcribe(audio, filename=filename, mime_type=mime_type)
            raise

    async def close(self) -> None:
        await self.primary.close()
        if self.fallback is not None:
            await self.fallback.close()


_client: AIClient | None = None
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


def get_ai() -> AIClient:
    global _client
    if _client is None:
        provider = resolve_provider(
            settings.ai_provider, gemini_key=settings.gemini_api_key, groq_key=settings.groq_api_key
        )
        if provider == "gemini":
            gemini = GeminiClient(
                settings.gemini_api_key,
                model=settings.gemini_model,
                agent_model=settings.gemini_agent_model,
                thinking_level=settings.gemini_thinking_level,
                media_resolution=settings.gemini_media_resolution,
                budget=get_budget(),
                prices=_prices(GEMINI_PRICES),
            )
            groq = None
            if settings.groq_api_key:
                groq = GroqClient(
                    settings.groq_api_key,
                    model=settings.groq_model,
                    stt_model=settings.groq_stt_model,
                    agent_model=settings.agent_model,
                    budget=get_budget(),
                    prices=_prices(GROQ_PRICES),
                )
            _client = ResilientAIClient(gemini, fallback=groq) if groq else gemini
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
    "ResilientAIClient",
    "close_ai",
    "get_ai",
    "get_budget",
    "resolve_provider",
]
