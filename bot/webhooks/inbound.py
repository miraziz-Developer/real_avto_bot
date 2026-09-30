"""Tashqi kanallar (Instagram Direct / kommentlar) uchun HTTP webhook.

Boshlang‘ich bosqich: Make.com yoki ManyChat Instagram xabarini shu yerga POST qiladi,
javobdagi `reply` matnini mijozga qaytaradi. Keyinchalik Meta Instagram Graph API webhookiga
o‘tilganda ham shu AI servis ishlatiladi.

POST /webhooks/inbound
    Header: X-Webhook-Secret: <INBOUND_WEBHOOK_SECRET>
    Body:   {"platform": "instagram", "user_id": "178414...", "username": "sardor_99",
             "name": "Sardor", "text": "Narxi qancha?"}
    Javob:  {"reply": "...", "lead_id": 12 | null}
"""

from __future__ import annotations

import hmac
import logging

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.services.ai import ClientContext
from bot.services.ai_sales import handle_ai_message

logger = logging.getLogger(__name__)

ALLOWED_PLATFORMS = frozenset({"instagram", "facebook", "web"})


def _str(v: object, max_len: int) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s[:max_len] if s else None


def create_inbound_app(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    secret: str,
) -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def inbound(request: web.Request) -> web.Response:
        given = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(given.encode(), secret.encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid_json"}, status=400)

        platform = (_str(body.get("platform"), 20) or "instagram").lower()
        user_id = _str(body.get("user_id"), 64)
        text = _str(body.get("text"), 2000)
        if platform not in ALLOWED_PLATFORMS or not user_id or not text:
            return web.json_response({"error": "platform, user_id va text majburiy"}, status=400)

        client = ClientContext(
            platform=platform,
            social_id=user_id,
            name=_str(body.get("name"), 200),
            username=(_str(body.get("username"), 100) or "").lstrip("@") or None,
        )
        async with session_factory() as session:
            try:
                reply = await handle_ai_message(session=session, bot=bot, client=client, text=text)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Inbound webhook xatosi (platform=%s)", platform)
                return web.json_response({"error": "internal"}, status=500)
        return web.json_response({"reply": reply.text, "lead_id": reply.lead_id})

    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/webhooks/inbound", inbound)
    return app


async def start_inbound_server(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    secret: str,
    port: int,
) -> web.AppRunner:
    runner = web.AppRunner(create_inbound_app(bot=bot, session_factory=session_factory, secret=secret))
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("Inbound webhook (Instagram/Make.com) :%s/webhooks/inbound da tinglamoqda", port)
    return runner
