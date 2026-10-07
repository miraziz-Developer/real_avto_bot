"""Google Gemini mijozi.

- Matn (chat_json, agent chat + asboblar) — Gemini'ning OpenAI-mos endpointi orqali, shuning uchun agent
  sikli (bot/agent/runner.py) Groq bilan bir xil formatda ishlaydi.
- Ovoz / dumaloq video / oddiy video — native generateContent: model nutqni ESHITADI va videoni KO'RADI
  (marka, rang, kuzov holati, spidometrdagi probeg). Whisper faqat ovozni matnga aylantirardi.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import aiohttp

from bot.ai.budget import AIBudget, Prices
from bot.ai.errors import AIBudgetExceeded, AIError

logger = logging.getLogger(__name__)

_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta"
_UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_TIMEOUT = aiohttp.ClientTimeout(total=120)
_RETRY_STATUSES = {429, 500, 502, 503, 504}
# So'rov tanasi ~20 MB bilan cheklangan; base64 hajmni ~33% oshiradi — kattaroq fayl Files API orqali
INLINE_MAX_BYTES = 14 * 1024 * 1024
_FILE_ACTIVE_TIMEOUT = 90

# Gemini 3.1 Flash-Lite (2026): matn/rasm/video $0.25, audio $0.50, javob $1.50 — 1M token uchun
DEFAULT_PRICES = Prices(input_per_m=0.25, output_per_m=1.50, audio_per_m=0.50)

MEDIA_SYSTEM_PROMPT = (
    "Sen Real Avto avtosalonining yordamchisisan. Senga Telegram'dan ovozli xabar, dumaloq video yoki video "
    "keladi. Odamlar asosan o'zbek tilida (lotin), ba'zan rus tilida yoki aralash gapiradi.\n"
    "Vazifa:\n"
    "1) NUTQ: aytilgan gaplarni so'zma-so'z, o'zbek lotin yozuvida yoz (ruscha gaplarni o'zicha qoldir). "
    "Mashina modellari va raqamlarni aniq yoz: Cobalt, Gentra, Nexia, Spark, Damas, Malibu, Tracker; "
    "«98 ming», «9500 dollar». Nutq bo'lmasa yoki faqat musiqa bo'lsa — «-».\n"
    "2) KO'RINISH (faqat video bo'lsa): videoda ANIQ ko'rinib turgan faktlar — marka/model, rang, kuzov "
    "holati (urilgan, chizilgan joylar), spidometrdagi probeg, ekrandagi yozuvlar (narx, telefon). "
    "Taxmin qilma: aniq ko'rinmasa yozma. Ovozli xabar bo'lsa yoki hech narsa aniq bo'lmasa — «-».\n"
    "Javobni aynan shu ikki qatorda ber:\n"
    "NUTQ: ...\n"
    "KO'RINISH: ..."
)

_LINE_RE = re.compile(r"^\s*\**\s*(NUTQ|KO['’‘`ʻʼ]?RINISH)\s*\**\s*:\s*\**\s*(.*)$", re.IGNORECASE)

_MIME_BY_EXT = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


def guess_mime(filename: str, mime_type: str | None = None) -> str:
    if mime_type:
        return mime_type
    name = (filename or "").lower()
    for ext, mime in _MIME_BY_EXT.items():
        if name.endswith(ext):
            return mime
    return "application/octet-stream"


def parse_media_answer(text: str) -> str:
    """«NUTQ: ... / KO'RINISH: ...» → bitta matn (pastdagi parser va agent uchun).

    Format buzilsa — butun javob nutq deb olinadi.
    """
    speech: list[str] = []
    visual: list[str] = []
    current: list[str] | None = None
    matched = False
    for line in (text or "").splitlines():
        m = _LINE_RE.match(line)
        if m:
            matched = True
            current = speech if m.group(1).upper().startswith("NUTQ") else visual
            current.append(m.group(2))
        elif current is not None and line.strip():
            current.append(line.strip())
    if not matched:
        return (text or "").strip()

    def clean(parts: list[str]) -> str:
        t = " ".join(p.strip() for p in parts).strip()
        return "" if t in {"", "-", "—", "–"} else t

    s, v = clean(speech), clean(visual)
    if v:
        return f"{s}\n[Videoda ko'rinadi]: {v}".strip()
    return s


def _usage_tokens_native(meta: dict | None) -> tuple[int, int, int]:
    """generateContent usageMetadata → (input, output, audio_input)."""
    if not meta:
        return 0, 0, 0
    inp = int(meta.get("promptTokenCount") or 0)
    out = int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0)
    audio = 0
    for d in meta.get("promptTokensDetails") or []:
        if str(d.get("modality", "")).upper() == "AUDIO":
            audio += int(d.get("tokenCount") or 0)
    return inp, out, audio


def _usage_tokens_openai(usage: dict | None) -> tuple[int, int]:
    if not usage:
        return 0, 0
    out = int(usage.get("completion_tokens") or 0)
    # Ba'zi javoblarda «thinking» alohida beriladi
    details = usage.get("completion_tokens_details") or {}
    if isinstance(details, dict) and details.get("reasoning_tokens") and out < int(details["reasoning_tokens"]):
        out += int(details["reasoning_tokens"])
    return int(usage.get("prompt_tokens") or 0), out


class GeminiClient:
    provider = "gemini"
    supports_video = True

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        agent_model: str | None = None,
        thinking_level: str = "low",
        media_resolution: str = "low",
        budget: AIBudget | None = None,
        prices: Prices = DEFAULT_PRICES,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.agent_model = agent_model or model
        self.thinking_level = thinking_level
        self.media_resolution = media_resolution
        self.budget = budget
        self.prices = prices
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # --- HTTP ----------------------------------------------------------------------------------
    async def _request(self, method: str, url: str, *, headers: dict | None = None, **kwargs: Any) -> dict:
        if not self.enabled:
            raise AIError("GEMINI_API_KEY sozlanmagan")
        # OpenAI-mos endpoint kalitni «Authorization: Bearer» da kutadi, native endpoint — x-goog-api-key da
        if url.startswith(_OPENAI_URL):
            auth = {"Authorization": f"Bearer {self._api_key}"}
        else:
            auth = {"x-goog-api-key": self._api_key}
        hdrs = {**auth, **(headers or {})}
        last = ""
        for attempt in range(3):
            try:
                async with self._http().request(method, url, headers=hdrs, **kwargs) as resp:
                    body = await resp.text()
                    if resp.status == 200:
                        return json.loads(body) if body else {}
                    last = f"HTTP {resp.status}: {body[:300]}"
                    if resp.status not in _RETRY_STATUSES:
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(1.5 * (attempt + 1))
        raise AIError(last or "Gemini javob bermadi")

    async def _before(self) -> None:
        if self.budget is not None:
            await self.budget.ensure_available()

    async def _charge(self, *, input_tokens: int, output_tokens: int, audio_tokens: int = 0) -> None:
        if self.budget is not None:
            await self.budget.add_cost(
                self.prices.cost(input_tokens=input_tokens, output_tokens=output_tokens, audio_tokens=audio_tokens)
            )

    # --- Matn (OpenAI-mos endpoint) ------------------------------------------------------------
    def _openai_payload(self, messages: list[dict], *, model: str, temperature: float, max_tokens: int) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            # Gemini'da «thinking» ham chiqish limitiga kiradi — javob kesilib qolmasligi uchun zaxira
            "max_tokens": max_tokens + (2048 if self.thinking_level else 0),
        }
        if self.thinking_level:
            payload["reasoning_effort"] = self.thinking_level
        return payload

    async def chat_json(self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 800) -> dict:
        await self._before()
        payload = self._openai_payload(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        payload["response_format"] = {"type": "json_object"}
        data = await self._request("POST", f"{_OPENAI_URL}/chat/completions", json=payload)
        inp, out = _usage_tokens_openai(data.get("usage"))
        await self._charge(input_tokens=inp, output_tokens=out)
        try:
            content = data["choices"][0]["message"]["content"] or ""
            # Ba'zan ```json ... ``` bilan o'raladi
            content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise AIError(f"JSON javob o'qilmadi: {e}") from e
        if not isinstance(result, dict):
            raise AIError("JSON obyekt kutilgan edi")
        return result

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 700,
    ) -> dict:
        """Agent qadami. Javob: {"content": str|None, "tool_calls": [...]} (OpenAI formatida)."""
        await self._before()
        payload = self._openai_payload(
            messages, model=model or self.agent_model, temperature=temperature, max_tokens=max_tokens
        )
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = await self._request("POST", f"{_OPENAI_URL}/chat/completions", json=payload)
        inp, out = _usage_tokens_openai(data.get("usage"))
        await self._charge(input_tokens=inp, output_tokens=out)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIError(f"Javob formati noto'g'ri: {e}") from e
        return {"content": msg.get("content"), "tool_calls": msg.get("tool_calls") or []}

    # --- Media (native generateContent) --------------------------------------------------------
    async def _upload_file(self, data: bytes, mime: str) -> tuple[str, str]:
        """Katta fayl (>14 MB) — Files API. Qaytaradi: (file_uri, name)."""
        if not self.enabled:
            raise AIError("GEMINI_API_KEY sozlanmagan")
        start_headers = {
            "x-goog-api-key": self._api_key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(data)),
            "X-Goog-Upload-Header-Content-Type": mime,
        }
        try:
            async with self._http().post(
                _UPLOAD_URL, headers=start_headers, json={"file": {"display_name": "telegram-media"}}
            ) as resp:
                upload_url = resp.headers.get("X-Goog-Upload-URL") or resp.headers.get("x-goog-upload-url")
                if resp.status != 200 or not upload_url:
                    raise AIError(f"Fayl yuklash boshlanmadi: HTTP {resp.status} {(await resp.text())[:200]}")
            async with self._http().post(
                upload_url,
                headers={
                    "X-Goog-Upload-Command": "upload, finalize",
                    "X-Goog-Upload-Offset": "0",
                    "Content-Length": str(len(data)),
                },
                data=data,
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise AIError(f"Fayl yuklanmadi: HTTP {resp.status} {body[:200]}")
                info = json.loads(body).get("file") or {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise AIError(f"Fayl yuklanmadi: {e}") from e
        name, uri = info.get("name", ""), info.get("uri", "")
        if not name or not uri:
            raise AIError("Fayl yuklandi, lekin URI qaytmadi")
        # Video qayta ishlanguncha (PROCESSING) kutamiz
        state = info.get("state", "")
        waited = 0
        while state not in ("", "ACTIVE") and waited < _FILE_ACTIVE_TIMEOUT:
            if state == "FAILED":
                raise AIError("Gemini faylni qayta ishlay olmadi")
            await asyncio.sleep(3)
            waited += 3
            meta = await self._request("GET", f"{_NATIVE_URL}/{name}")
            state = meta.get("state", "")
        if state not in ("", "ACTIVE"):
            raise AIError("Fayl tayyor bo'lmadi (vaqt tugadi)")
        return uri, name

    async def _delete_file(self, name: str) -> None:
        try:
            async with self._http().delete(
                f"{_NATIVE_URL}/{name}", headers={"x-goog-api-key": self._api_key}
            ):
                pass
        except Exception:
            logger.debug("Gemini faylini o'chirib bo'lmadi: %s", name)

    async def analyze_media(self, data: bytes, *, mime_type: str) -> str:
        """Ovoz/video → «nutq + [Videoda ko'rinadi]: ...» matni (bo'sh — hech narsa tushunilmadi)."""
        if not data:
            return ""
        await self._before()
        uploaded: str | None = None
        if len(data) <= INLINE_MAX_BYTES:
            media_part: dict[str, Any] = {
                "inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")}
            }
        else:
            uri, uploaded = await self._upload_file(data, mime_type)
            media_part = {"file_data": {"mime_type": mime_type, "file_uri": uri}}
        gen_cfg: dict[str, Any] = {"temperature": 0, "maxOutputTokens": 1024 + (2048 if self.thinking_level else 0)}
        if self.thinking_level:
            gen_cfg["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        if self.media_resolution and mime_type.startswith("video/"):
            gen_cfg["mediaResolution"] = f"MEDIA_RESOLUTION_{self.media_resolution.upper()}"
        kind = "video" if mime_type.startswith("video/") else "ovozli xabar"
        body = {
            "systemInstruction": {"parts": [{"text": MEDIA_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [media_part, {"text": f"Bu {kind}. Ko'rsatilgan formatda javob ber."}]}],
            "generationConfig": gen_cfg,
        }
        try:
            resp = await self._request("POST", f"{_NATIVE_URL}/models/{self.model}:generateContent", json=body)
        finally:
            if uploaded:
                await self._delete_file(uploaded)
        inp, out, audio = _usage_tokens_native(resp.get("usageMetadata"))
        await self._charge(input_tokens=inp, output_tokens=out, audio_tokens=audio)
        try:
            parts = resp["candidates"][0]["content"].get("parts") or []
        except (KeyError, IndexError, TypeError):
            # Xavfsizlik filtri yoki bo'sh javob
            return ""
        text = "\n".join(p.get("text", "") for p in parts if not p.get("thought"))
        return parse_media_answer(text)

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg", mime_type: str | None = None) -> str:
        """Groq bilan bir xil interfeys: ovoz/video → matn. Gemini videoni ko'radi ham."""
        return await self.analyze_media(audio, mime_type=guess_mime(filename, mime_type))


__all__ = ["AIBudgetExceeded", "AIError", "GeminiClient", "guess_mime", "parse_media_answer"]
