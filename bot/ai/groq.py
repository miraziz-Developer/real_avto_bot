"""Groq (OpenAI-mos API) mijozi: JSON javobli chat va ovozni matnga aylantirish."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.groq.com/openai/v1"
_TIMEOUT = aiohttp.ClientTimeout(total=60)
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class AIError(RuntimeError):
    pass


class GroqClient:
    def __init__(self, api_key: str, *, model: str, stt_model: str) -> None:
        self._api_key = api_key
        self.model = model
        self.stt_model = stt_model
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=_TIMEOUT,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _post(self, path: str, **kwargs: Any) -> dict:
        if not self.enabled:
            raise AIError("GROQ_API_KEY sozlanmagan")
        last: str = ""
        for attempt in range(3):
            try:
                async with self._http().post(f"{_BASE_URL}{path}", **kwargs) as resp:
                    body = await resp.text()
                    if resp.status == 200:
                        return json.loads(body)
                    last = f"HTTP {resp.status}: {body[:300]}"
                    if resp.status not in _RETRY_STATUSES:
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(1.5 * (attempt + 1))
        raise AIError(last or "Groq javob bermadi")

    async def chat_json(self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 800) -> dict:
        """Model javobini JSON obyekt sifatida qaytaradi."""
        data = await self._post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
        )
        try:
            content = data["choices"][0]["message"]["content"]
            out = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise AIError(f"JSON javob o'qilmadi: {e}") from e
        if not isinstance(out, dict):
            raise AIError("JSON obyekt kutilgan edi")
        return out

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 700,
    ) -> dict:
        """Bitta model qadami. Javob: {"content": str|None, "tool_calls": [...]} (OpenAI formatidagi message)."""
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = await self._post("/chat/completions", json=payload)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIError(f"Javob formati noto'g'ri: {e}") from e
        return {"content": msg.get("content"), "tool_calls": msg.get("tool_calls") or []}

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg") -> str:
        """Ovozli xabar / dumaloq video ovozini matnga aylantirish."""
        form = aiohttp.FormData()
        form.add_field("file", audio, filename=filename)
        form.add_field("model", self.stt_model)
        # Til aniq berilmasa Whisper o'zbekchani ko'pincha turkcha deb taniydi
        if settings.groq_stt_language:
            form.add_field("language", settings.groq_stt_language)
        form.add_field("response_format", "json")
        data = await self._post("/audio/transcriptions", data=form)
        return str(data.get("text") or "").strip()


_client: GroqClient | None = None


def get_ai() -> GroqClient:
    global _client
    if _client is None:
        _client = GroqClient(settings.groq_api_key, model=settings.groq_model, stt_model=settings.groq_stt_model)
    return _client
