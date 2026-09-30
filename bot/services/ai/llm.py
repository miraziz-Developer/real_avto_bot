"""OpenAI-mos Chat Completions mijoz (OpenAI, Google Gemini, OpenRouter va h.k.).

Gemini uchun: AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import aiohttp

logger = logging.getLogger(__name__)


class LlmError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ChatClient(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Assistant xabarini qaytaradi: {"role": "assistant", "content": ..., "tool_calls": [...]}."""


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.4,
        timeout_seconds: float = 40.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._temperature = temperature
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_retries = max(0, max_retries)
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with self._get_session().post(self._url, json=payload, headers=headers) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        # 429/5xx — vaqtinchalik; boshqa 4xx (kalit/model xato) — qayta urinishdan foyda yo‘q.
                        raise LlmError(
                            f"AI API {resp.status}: {body[:500]}",
                            retryable=resp.status == 429 or resp.status >= 500,
                        )
                    data = await resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LlmError(f"AI API javobida choices yo‘q: {str(data)[:300]}")
                return choices[0].get("message") or {}
            except (aiohttp.ClientError, asyncio.TimeoutError, LlmError) as e:
                last_error = e
                if attempt >= self._max_retries or not getattr(e, "retryable", True):
                    break
                await asyncio.sleep(1.5 * (attempt + 1))
        logger.warning("AI so‘rov muvaffaqiyatsiz: %s", last_error)
        raise LlmError(str(last_error))
