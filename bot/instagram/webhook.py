"""Instagram webhook: Meta → bizning server. Direct xabarlar va kommentlarga savdo agenti javob beradi.

Meta panelida: Webhooks → Instagram → Callback URL `https://DOMEN/webhooks/instagram`, Verify token = IG_VERIFY_TOKEN,
obunalar: `messages`, `comments`. Har POST X-Hub-Signature-256 (IG_APP_SECRET) bilan tekshiriladi.

Qoidalar (Telegram bilan bir xil):
  • agent faqat bazadan javob beradi, tayyor mijozni menejerga topshiradi (lead kartasi Telegram'da adminlarga);
  • menejer kartaga reply qilsa — javob Instagram Direct'ga ketadi;
  • akkaunt egasi Instagram ilovasidan o'zi yozsa — AI shu mijoz bilan BUSINESS_OWNER_PAUSE_HOURS jim;
  • komment: savol bo'lsa ochiq qisqa javob + batafsil Direct'da (private reply).

Eslatma: Instagram mijozi uchun `leads.telegram_id` ustunida IGSID saqlanadi (channel="instagram").
IGSID lar (~10^16) Telegram ID laridan (~10^10) ancha katta — to'qnashmaydi.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import time

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.agent.service import generate_agent_reply
from bot.agent.tools import AgentContext
from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.models import Car, Lead
from bot.db.repositories import CrmRepository
from bot.handlers.channel_comments import has_buy_intent, is_question
from bot.handlers.sales_agent import forward_text_to_manager
from bot.instagram.client import IGError, InstagramClient
from bot.services.car_cards import notify_admins_text
from bot.services.lead_cards import lead_score

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/instagram"
COMMENT_COOLDOWN_SECONDS = 600
MAX_IG_PHOTOS = 4
PUBLIC_COMMENT_REPLY = "Assalomu alaykum! Batafsil javobni Direct'ga yubordik 📩"


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


class InstagramWebhook:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker[AsyncSession], client: InstagramClient) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.client = client
        self._locks: dict[str, asyncio.Lock] = {}
        self._comment_replied: dict[tuple[str, str], float] = {}
        self._tasks: set[asyncio.Task] = set()

    def _lock(self, igsid: str) -> asyncio.Lock:
        if len(self._locks) > 5000:
            self._locks.clear()
        return self._locks.setdefault(igsid, asyncio.Lock())

    async def process(self, payload: dict) -> None:
        for entry in payload.get("entry") or []:
            for ev in entry.get("messaging") or []:
                try:
                    await self._on_message(ev)
                except Exception:
                    logger.exception("Instagram xabarini qayta ishlash xatosi")
            for ch in entry.get("changes") or []:
                if ch.get("field") == "comments":
                    try:
                        await self._on_comment(ch.get("value") or {})
                    except Exception:
                        logger.exception("Instagram kommentini qayta ishlash xatosi")

    # --- Lead ---------------------------------------------------------------------------------

    async def _open_lead(self, leads: LeadRepository, igsid: str, username: str | None) -> Lead:
        lead = await leads.get_open(int(igsid))
        if lead is not None:
            if username and not lead.username:
                lead.username = username
            return lead
        name = None
        if not username:
            profile = await self.client.get_profile(igsid)
            username = profile.get("username")
            name = profile.get("name")
        lead, _ = await leads.get_or_create_open(
            int(igsid),
            name=name or (f"@{username}" if username else "Instagram mijoz"),
            username=username,
            channel="instagram",
        )
        return lead

    def _photo_sender(self, igsid: str):
        async def send(car: Car, file_ids: list[str], caption: str) -> int:
            # Instagram rasmni URL orqali oladi — katalogning ochiq rasm proksisi kerak (HTTPS)
            if not settings.catalog_url.startswith("https://"):
                return 0
            sent = 0
            for idx in range(min(len(file_ids), MAX_IG_PHOTOS)):
                try:
                    await self.client.send_image(igsid, f"{settings.catalog_url}/api/public/photo/{car.id}/{idx}")
                    sent += 1
                except IGError as e:
                    logger.warning("Instagram rasm yuborilmadi (car #%s): %s", car.id, e)
                    break
            if sent:
                await self.client.send_text(igsid, caption)
            return sent

        return send

    async def _agent_answer(self, igsid: str, text: str, *, username: str | None, via_comment: str | None = None) -> None:
        """Mijoz matniga agent javobi. via_comment — birinchi javob kommentga shaxsiy javob (private reply) bo'ladi."""
        async with self._lock(igsid), self.session_factory() as session:
            leads = LeadRepository(session)
            cars = CarRepository(session)
            crm = CrmRepository(session)
            lead = await self._open_lead(leads, igsid, username)
            await leads.add_message(lead, "user", f"[komment] {text}" if via_comment else text)
            if leads.is_human(lead):
                await forward_text_to_manager(self.bot, leads, lead, text, source=" · Instagram")
                await session.commit()
                return
            ctx = AgentContext(
                bot=self.bot,
                chat_id=int(igsid),
                lead=lead,
                cars=cars,
                leads=leads,
                crm=crm,
                buttons_supported=False,
                photo_sender=self._photo_sender(igsid),
            )
            reply, _ = await generate_agent_reply(ctx, text)
            await leads.add_message(lead, "assistant", reply)
            lead.score = lead_score(lead)
            await session.commit()
        try:
            if via_comment:
                await self.client.private_reply(via_comment, reply)
            else:
                await self.client.send_text(igsid, reply)
        except IGError as e:
            logger.warning("Instagram javobi yuborilmadi (%s): %s", igsid, e)

    # --- Direct --------------------------------------------------------------------------------

    async def _on_message(self, ev: dict) -> None:
        msg = ev.get("message") or {}
        sender = str((ev.get("sender") or {}).get("id") or "")
        recipient = str((ev.get("recipient") or {}).get("id") or "")
        if not msg or msg.get("is_deleted") or not sender:
            return
        if msg.get("is_echo"):
            if str(msg.get("mid")) in self.client.sent_mids:
                return  # o'zimiz API orqali yuborgan javob
            # Akkaunt egasi Instagram ilovasidan mijozga o'zi yozdi → AI shu mijoz bilan jim turadi
            async with self.session_factory() as session:
                leads = LeadRepository(session)
                lead = await leads.get_open(int(recipient)) if recipient.isdigit() else None
                if lead is not None:
                    await leads.pause_for_owner(lead, hours=settings.business_owner_pause_hours)
                    await leads.add_message(lead, "admin", msg.get("text") or "[media]")
                    await session.commit()
            return
        if sender == settings.ig_account_id or not sender.isdigit():
            return
        text = (msg.get("text") or "").strip()
        if not text:
            # Rasm/ovoz — AI ko'rmaydi, menejerga xabar
            async with self.session_factory() as session:
                leads = LeadRepository(session)
                lead = await self._open_lead(leads, sender, None)
                await leads.add_message(lead, "user", "[media]")
                await forward_text_to_manager(
                    self.bot, leads, lead, "[rasm/ovoz yubordi — Instagram'da ko'ring]", source=" · Instagram"
                )
                await session.commit()
            return
        await self._agent_answer(sender, text, username=None)

    # --- Kommentlar ---------------------------------------------------------------------------

    def _comment_cooldown_ok(self, user_id: str, media_id: str) -> bool:
        now = time.monotonic()
        key = (user_id, media_id)
        if now - self._comment_replied.get(key, float("-inf")) < COMMENT_COOLDOWN_SECONDS:
            return False
        if len(self._comment_replied) > 20000:
            self._comment_replied.clear()
        self._comment_replied[key] = now
        return True

    async def _on_comment(self, v: dict) -> None:
        if not settings.comments_enabled:
            return
        comment_id = str(v.get("id") or "")
        text = (v.get("text") or "").strip()
        frm = v.get("from") or {}
        user_id = str(frm.get("id") or "")
        username = frm.get("username")
        media_id = str((v.get("media") or {}).get("id") or "")
        if not comment_id or not text or not user_id.isdigit() or user_id == settings.ig_account_id:
            return
        if v.get("parent_id"):
            return  # komment ichidagi javoblar (masalan, bizning javobimiz ostidagi) — Direct'da davom etadi
        if not is_question(text) or not self._comment_cooldown_ok(user_id, media_id):
            return
        try:
            await self.client.reply_comment(comment_id, PUBLIC_COMMENT_REPLY)
        except IGError as e:
            logger.warning("Instagram kommentiga javob yozilmadi: %s", e)
        await self._agent_answer(user_id, text, username=username, via_comment=comment_id)
        if has_buy_intent(text):
            who = f"@{username}" if username else user_id
            await notify_admins_text(
                self.bot,
                f"💬 <b>Instagram kommentida xarid niyati</b>\n👤 {html.escape(who)}\n«{html.escape(text[:300])}»",
            )

    # --- HTTP ---------------------------------------------------------------------------------

    async def handle_verify(self, request: web.Request) -> web.Response:
        q = request.query
        token_ok = bool(settings.ig_verify_token) and hmac.compare_digest(
            q.get("hub.verify_token", ""), settings.ig_verify_token
        )
        if q.get("hub.mode") == "subscribe" and token_ok:
            return web.Response(text=q.get("hub.challenge", ""))
        return web.Response(status=403)

    async def handle_event(self, request: web.Request) -> web.Response:
        body = await request.read()
        if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), settings.ig_app_secret):
            logger.warning("Instagram webhook: imzo noto'g'ri — rad etildi")
            return web.Response(status=403)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400)
        # Meta tez javob kutadi — qayta ishlashni fonda bajaramiz (xatolar process() ichida log qilinadi)
        task = asyncio.create_task(self.process(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return web.Response(text="EVENT_RECEIVED")

    def app(self) -> web.Application:
        application = web.Application(client_max_size=1024 * 1024)
        application.router.add_get(WEBHOOK_PATH, self.handle_verify)
        application.router.add_post(WEBHOOK_PATH, self.handle_event)

        async def healthz(_request: web.Request) -> web.Response:
            return web.Response(text="ok")

        application.router.add_get("/healthz", healthz)
        return application


async def start_instagram_server(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession], client: InstagramClient
) -> web.AppRunner | None:
    if not settings.instagram_enabled:
        return None
    if not (client.enabled and settings.ig_app_secret and settings.ig_verify_token):
        logger.error("INSTAGRAM_ENABLED=true, lekin IG_ACCESS_TOKEN / IG_APP_SECRET / IG_VERIFY_TOKEN to'liq emas")
        return None
    runner = web.AppRunner(InstagramWebhook(bot, session_factory, client).app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.ig_webhook_port).start()
    logger.info("Instagram webhook: :%s%s", settings.ig_webhook_port, WEBHOOK_PATH)
    return runner
