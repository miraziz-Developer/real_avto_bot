"""Instagram Graph API mijozi: Direct xabar, rasm, komment javobi, kommentga shaxsiy javob."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Instagram Direct matn chegarasi
IG_TEXT_LIMIT = 1000


class IGError(RuntimeError):
    pass


def split_text(text: str, limit: int = IG_TEXT_LIMIT) -> list[str]:
    """Uzun javobni Instagram chegarasiga moslab bo'lish (iloji boricha qator chegarasida)."""
    text = text.strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


class InstagramClient:
    def __init__(self, access_token: str, *, version: str) -> None:
        self._token = access_token
        self.base = f"https://graph.instagram.com/{version}"
        self._session: aiohttp.ClientSession | None = None
        # API orqali o'zimiz yuborgan xabarlar — webhook «echo» sini egasining qo'lda yozganidan ajratish uchun
        self.sent_mids: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=_TIMEOUT, headers={"Authorization": f"Bearer {self._token}"}
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        if not self.enabled:
            raise IGError("IG_ACCESS_TOKEN sozlanmagan")
        last = ""
        for attempt in range(3):
            try:
                async with self._http().request(method, f"{self.base}{path}", json=json, params=params) as resp:
                    data: dict[str, Any] = await resp.json(content_type=None)
                    if resp.status == 200:
                        return data
                    last = f"HTTP {resp.status}: {str(data)[:300]}"
                    if resp.status < 500 and resp.status != 429:
                        break
            except (TimeoutError, aiohttp.ClientError) as e:
                last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(1.5 * (attempt + 1))
        raise IGError(last or "Instagram javob bermadi")

    def _remember(self, data: dict) -> None:
        mid = data.get("message_id")
        if mid:
            if len(self.sent_mids) > 5000:
                self.sent_mids.clear()
            self.sent_mids.add(str(mid))

    async def send_text(self, igsid: str | int, text: str) -> None:
        for part in split_text(text):
            data = await self._request(
                "POST", "/me/messages", json={"recipient": {"id": str(igsid)}, "message": {"text": part}}
            )
            self._remember(data)

    async def send_image(self, igsid: str | int, url: str) -> None:
        data = await self._request(
            "POST",
            "/me/messages",
            json={"recipient": {"id": str(igsid)}, "message": {"attachment": {"type": "image", "payload": {"url": url}}}},
        )
        self._remember(data)

    async def reply_comment(self, comment_id: str, text: str) -> None:
        await self._request("POST", f"/{comment_id}/replies", json={"message": text[:IG_TEXT_LIMIT]})

    async def private_reply(self, comment_id: str, text: str) -> None:
        """Kommentga Direct orqali javob — mijoz akkauntga hech qachon yozmagan bo'lsa ham (bir marta, 7 kun ichida)."""
        data = await self._request(
            "POST",
            "/me/messages",
            json={"recipient": {"comment_id": comment_id}, "message": {"text": text[:IG_TEXT_LIMIT]}},
        )
        self._remember(data)

    async def get_profile(self, igsid: str | int) -> dict:
        try:
            return await self._request("GET", f"/{igsid}", params={"fields": "name,username"})
        except IGError as e:
            logger.info("Instagram profilini olib bo'lmadi (%s): %s", igsid, e)
            return {}


_client: InstagramClient | None = None


def get_ig() -> InstagramClient:
    global _client
    if _client is None:
        _client = InstagramClient(settings.ig_access_token, version=settings.ig_graph_version)
    return _client
