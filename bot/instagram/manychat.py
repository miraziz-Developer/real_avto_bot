"""ManyChat → bot: Instagram kommentlari va Direct xabarlariga savdo agenti javobi.

Instagram akkaunt ManyChat'ga ulanadi (Meta App Review kerak emas). ManyChat avtomatizatsiyasi
«External Request» bilan POST {PUBLIC_HOST}/manychat/instagram ga yuboradi (sarlavha:
`Authorization: Bearer MANYCHAT_SECRET`), javobdagi matnni mijozga Direct'da yuboradi.

So'rov (JSON, ManyChat o'zgaruvchilari bilan):
    user_id        {{user_id}} — ManyChat obunachi ID si (majburiy)
    event          "comment" yoki "message"
    text           komment / xabar matni
    ig_username    {{ig_username}};  name — {{full_name}}
    attachment_url ovozli xabar / video havolasi (ixtiyoriy — AI tinglaydi)
    car            shu post qaysi mashina (ixtiyoriy: avtomatizatsiya postga bog'langan bo'lsa, mashina raqami)
    post_url, media_id, caption — post haqida nima ma'lum bo'lsa (ixtiyoriy)

Javob: ManyChat Dynamic Block (version v2) + `reply`, `public_reply`, `car_id` maydonlari (Response mapping uchun).

Qaysi mashina — bot/instagram/car_match.py. Topilmasa va post ma'lum bo'lsa, adminlardan Telegram'da so'raladi.
Mijoz bazada `leads.telegram_id = -(10^13 + user_id)` bilan saqlanadi (channel="manychat") — Telegram
ID lari bilan to'qnashmaydi.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import logging
import re
from datetime import UTC, datetime
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.agent.service import generate_agent_reply
from bot.agent.tools import AgentContext
from bot.ai import get_ai, get_budget
from bot.ai.errors import AIError
from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.models import Car, CarStatus, InstagramPost, Lead
from bot.db.repositories import CrmRepository
from bot.handlers.channel_comments import has_buy_intent, is_question
from bot.handlers.sales_agent import forward_text_to_manager
from bot.instagram.car_match import CarMatch, post_ref, resolve_car
from bot.instagram.client import IGError, InstagramClient, split_text
from bot.services.car_cards import notify_admins_text
from bot.services.lead_cards import lead_score

logger = logging.getLogger(__name__)

MANYCHAT_PATH = "/manychat/instagram"
MANYCHAT_API = "https://api.manychat.com"
LEAD_ID_OFFSET = 10**13
MAX_PHOTOS = 4
MEDIA_MAX_BYTES = 19 * 1024 * 1024
ADMIN_CAR_CHOICES = 8
PUBLIC_COMMENT_REPLY = "Assalomu alaykum! Javobni Direct'ga yubordik 📩"
HOLD_REPLY = "Assalomu alaykum! Savolingizni oldik, bir daqiqada javob beramiz 🙏"
LATE_FALLBACK_REPLY = "Assalomu alaykum! Savolingizni oldik — menejerimiz tez orada javob beradi 🙏"
_PLACEHOLDER_RE = re.compile(r"^\s*\{\{.*\}\}\s*$")


def lead_key(subscriber_id: str | int) -> int:
    return -(LEAD_ID_OFFSET + int(subscriber_id))


def subscriber_of(lead: Lead) -> int:
    return -lead.telegram_id - LEAD_ID_OFFSET


def _field(payload: dict, *names: str, limit: int = 2000) -> str:
    """ManyChat bo'sh o'zgaruvchini ba'zan «{{...}}» ko'rinishida yuboradi — uni bo'sh deb olamiz."""
    for name in names:
        value = payload.get(name)
        if value is None:
            continue
        value = str(value).strip()
        if value and not _PLACEHOLDER_RE.match(value):
            return value[:limit]
    return ""


def dynamic_block(texts: list[str], image_urls: list[str] | None = None) -> dict:
    messages: list[dict[str, Any]] = [{"type": "image", "url": u} for u in image_urls or []]
    for t in texts:
        messages.extend({"type": "text", "text": part} for part in split_text(t))
    return {"version": "v2", "content": {"type": "instagram", "messages": messages, "actions": [], "quick_replies": []}}


class ManyChatAPI:
    """Kechikkan AI javobi va menejer javoblarini mijozga yuborish (MANYCHAT_API_KEY kerak)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def send_text(self, subscriber_id: int, text: str, image_urls: list[str] | None = None) -> None:
        if not self.enabled:
            raise IGError("MANYCHAT_API_KEY sozlanmagan")
        body = {"subscriber_id": subscriber_id, "data": dynamic_block([text], image_urls)}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as http, http.post(
            f"{MANYCHAT_API}/fb/sending/sendContent",
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or (isinstance(data, dict) and data.get("status") == "error"):
                raise IGError(f"ManyChat HTTP {resp.status}: {str(data)[:300]}")


def car_choice_kb(post: InstagramPost, cars: list[Car]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"#{c.id} {c.title}"[:60], callback_data=f"igpost:{post.id}:{c.id}")] for c in cars
    ]
    rows.append([InlineKeyboardButton(text="🚫 Mashina posti emas", callback_data=f"igpost:{post.id}:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class ManyChatBridge:
    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ig: InstagramClient | None = None,
        api: ManyChatAPI | None = None,
    ) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.ig = ig
        self.api = api or ManyChatAPI(settings.manychat_api_key)
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    def _lock(self, key: str) -> asyncio.Lock:
        if len(self._locks) > 5000:
            self._locks.clear()
        return self._locks.setdefault(key, asyncio.Lock())

    # --- Post va media ---------------------------------------------------------------------------

    async def _find_post(self, payload: dict, *, is_comment: bool, username: str, text: str) -> dict:
        """Post haqida ma'lumot: so'rovdagisi, bo'lmasa (IG token bo'lsa) Graph API'dan."""
        media_id = _field(payload, "media_id", limit=60)
        info = {
            "media_id": media_id,
            "permalink": _field(payload, "post_url", "permalink", limit=300),
            "caption": _field(payload, "caption", limit=4000),
        }
        if self.ig is None or not self.ig.enabled:
            return info
        try:
            if media_id and not info["caption"]:
                media = await asyncio.wait_for(self.ig.get_media(media_id), timeout=3)
            elif is_comment and not media_id and not info["permalink"] and username and text:
                media = await asyncio.wait_for(self.ig.find_comment_media(username, text), timeout=4)
            else:
                media = None
        except (IGError, TimeoutError) as e:
            logger.info("Instagram post ma'lumotini olib bo'lmadi: %s", e)
            media = None
        if media:
            info["media_id"] = info["media_id"] or str(media.get("id") or "")
            info["caption"] = info["caption"] or (media.get("caption") or "")
            info["permalink"] = info["permalink"] or (media.get("permalink") or "")
        return info

    async def _listen(self, url: str, user_key: str) -> str:
        """Ovozli xabar / video: yuklab olib AI'ga eshittiramiz (Gemini videoni ko'radi ham)."""
        ai = get_ai()
        if not ai.enabled or not await get_budget().allow_user(user_key):
            return ""
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as http, http.get(url) as resp:
                if resp.status != 200 or (resp.content_length or 0) > MEDIA_MAX_BYTES:
                    return ""
                data = await resp.content.read(MEDIA_MAX_BYTES + 1)
                mime = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        except (TimeoutError, aiohttp.ClientError) as e:
            logger.info("ManyChat media yuklanmadi: %s", e)
            return ""
        if len(data) > MEDIA_MAX_BYTES or not data:
            return ""
        if mime.startswith("video/"):
            filename = "video.mp4"
        elif mime.startswith("audio/"):
            filename = "audio.mp4" if "mp4" in mime else "audio.ogg"
        else:
            return ""  # rasm — AI javobi uchun emas, menejer ko'radi
        try:
            return await ai.transcribe(data, filename=filename, mime_type=mime)
        except AIError as e:
            logger.warning("ManyChat ovoz/videosi o'qilmadi: %s", e)
            return ""

    # --- Asosiy oqim -------------------------------------------------------------------------------

    async def answer(self, payload: dict) -> dict:
        """Javob tayyorlash (sinxron qism): {reply, public_reply, car_id, images}."""
        sub = _field(payload, "user_id", "subscriber_id", limit=40)
        if not sub.isdigit():
            raise ValueError("user_id kerak")
        is_comment = _field(payload, "event", "type", limit=20).lower().startswith("comment")
        text = _field(payload, "text", "last_input_text", "comment", limit=2000)
        username = _field(payload, "ig_username", "username", limit=64).lstrip("@")
        name = _field(payload, "name", "full_name", limit=200)
        attachment = _field(payload, "attachment_url", limit=1000)
        if attachment.startswith("https://"):
            heard = await self._listen(attachment, f"mc:{sub}")
            if heard:
                kind = "Video" if "video" in attachment.lower() else "Ovozli xabar"
                text = "\n".join(t for t in (text, f"[{kind}]: {heard}") if t)
            elif not text:
                text = "[ovozli xabar / video yubordi — eshitib bo'lmadi]"
        result: dict[str, Any] = {"reply": "", "public_reply": "", "car_id": None, "images": []}
        if not text:
            return result
        if is_comment and not (is_question(text) or has_buy_intent(text)):
            return result  # «zo'r 🔥» — javob shart emas
        post_info = await self._find_post(payload, is_comment=is_comment, username=username, text=text)
        ref = post_ref(post_info["media_id"], post_info["permalink"])
        images: list[str] = []

        async def photo_sender(car: Car, file_ids: list[str], caption: str) -> int:
            if not settings.catalog_url.startswith("https://"):
                return 0
            n = min(len(file_ids), MAX_PHOTOS)
            images.extend(f"{settings.catalog_url}/api/public/photo/{car.id}/{i}" for i in range(n))
            return n

        async with self._lock(sub), self.session_factory() as session:
            cars = CarRepository(session)
            leads = LeadRepository(session)
            key = lead_key(sub)
            lead = await leads.get_open(key)
            if lead is None:
                lead, _ = await leads.get_or_create_open(
                    key,
                    name=name or (f"@{username}" if username else "Instagram mijoz"),
                    username=username or None,
                    channel="manychat",
                )
            elif username and not lead.username:
                lead.username = username
            match = await resolve_car(
                cars,
                explicit=_field(payload, "car", "car_id", limit=20) or None,
                ref=ref,
                caption=post_info["caption"] or None,
                permalink=post_info["permalink"] or None,
                text=text,
            )
            if match.car is not None and (is_comment or match.how in ("explicit", "mapped", "caption")):
                lead.car_id = match.car.id  # mijoz aynan shu post (mashina) haqida so'rayapti
            await leads.add_message(lead, "user", self._history_text(text, match, is_comment=is_comment))
            if leads.is_human(lead):
                await forward_text_to_manager(self.bot, leads, lead, text, source=" · Instagram")
                await session.commit()
                return result
            ctx = AgentContext(
                bot=self.bot,
                chat_id=lead.telegram_id,
                lead=lead,
                cars=cars,
                leads=leads,
                crm=CrmRepository(session),
                buttons_supported=False,
                photo_sender=photo_sender,
            )
            reply, _ = await generate_agent_reply(ctx, text)
            await leads.add_message(lead, "assistant", reply)
            lead.score = lead_score(lead)
            ask_post = match.post if (match.car is None and match.post is not None and match.post.asked_admin_at is None) else None
            if ask_post is not None:
                ask_post.asked_admin_at = datetime.now(UTC)
            choices = await cars.search_offerable(limit=ADMIN_CAR_CHOICES) if ask_post is not None else []
            await session.commit()
            result.update(reply=reply, car_id=match.car.id if match.car else None, images=images)
        if is_comment:
            result["public_reply"] = PUBLIC_COMMENT_REPLY
        who = f"@{username}" if username else (name or sub)
        if ask_post is not None:
            await self._ask_admins_about_post(ask_post, choices, who=who, text=text)
        if is_comment and has_buy_intent(text):
            await notify_admins_text(
                self.bot,
                f"💬 <b>Instagram kommentida xarid niyati</b>\n👤 {html.escape(who)}\n«{html.escape(text[:300])}»",
            )
        logger.info("ManyChat %s: sub=%s car=%s (%s)", "komment" if is_comment else "xabar", sub, result["car_id"], match.how)
        return result

    @staticmethod
    def _history_text(text: str, match: CarMatch, *, is_comment: bool) -> str:
        if match.car is None:
            return f"[Instagram komment] {text}" if is_comment else text
        car = match.car
        status = {CarStatus.SOLD: ", SOTILGAN", CarStatus.RESERVED: ", bron", CarStatus.ARCHIVED: ", sotuvda emas"}.get(
            car.status, ""
        )
        where = "posti ostidagi komment" if is_comment else "posti haqida xabar"
        return f"[Instagram: #{car.id} {car.title}{status} {where}] {text}"

    async def _ask_admins_about_post(self, post: InstagramPost, cars: list[Car], *, who: str, text: str) -> None:
        if not settings.admin_telegram_ids:
            return
        link = f'<a href="{html.escape(post.permalink)}">post</a>' if post.permalink else f"post <code>{html.escape(post.ref)}</code>"
        body = (
            f"📸 <b>Instagram {link} qaysi mashina?</b>\n"
            f"👤 {html.escape(who)}: «{html.escape(text[:200])}»\n"
            "Tanlang — keyingi kommentlarga shu mashina ma'lumoti bilan javob beriladi. "
            f"Ro'yxatda bo'lmasa: <code>/ig {html.escape(post.ref)} ID</code>"
        )
        kb = car_choice_kb(post, cars)
        for aid in settings.admin_telegram_ids:
            try:
                await self.bot.send_message(aid, body, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
            except (TelegramBadRequest, TelegramForbiddenError) as e:
                logger.warning("Admin %s ga Instagram post so'rovi yuborilmadi: %s", aid, e)

    # --- HTTP ---------------------------------------------------------------------------------------

    def _authorized(self, request: web.Request) -> bool:
        secret = settings.manychat_secret
        if not secret:
            return False
        auth = request.headers.get("Authorization", "")
        given = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("X-Api-Key", "")
        return bool(given) and hmac.compare_digest(given.strip(), secret)

    async def _deliver_late(self, task: asyncio.Task, sub: int) -> None:
        try:
            result = await task
        except Exception:
            logger.exception("ManyChat: kechikkan javob tayyorlanmadi")
            return
        if result.get("reply"):
            try:
                await self.api.send_text(sub, result["reply"], result.get("images"))
            except (IGError, TimeoutError, aiohttp.ClientError) as e:
                logger.warning("ManyChat: kechikkan javob yuborilmadi (%s): %s", sub, e)

    async def handle(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = await request.json()
        except ValueError:
            return web.json_response({"error": "json kerak"}, status=400)
        if not isinstance(payload, dict) or not _field(payload, "user_id", "subscriber_id", limit=40).isdigit():
            return web.json_response({"error": "user_id kerak"}, status=400)
        task = asyncio.create_task(self.answer(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=settings.manychat_reply_timeout)
        except TimeoutError:
            # ManyChat ~10 soniyadan ko'p kutmaydi: hozircha qisqa javob, tayyor bo'lgach API orqali yuboramiz
            sub = int(_field(payload, "user_id", "subscriber_id", limit=40))
            if self.api.enabled:
                late = asyncio.create_task(self._deliver_late(task, sub))
                self._tasks.add(late)
                late.add_done_callback(self._tasks.discard)
                reply = HOLD_REPLY
            else:
                reply = LATE_FALLBACK_REPLY
            result = {"reply": reply, "public_reply": "", "car_id": None, "images": []}
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            logger.exception("ManyChat so'rovi xatosi")
            result = {"reply": LATE_FALLBACK_REPLY, "public_reply": "", "car_id": None, "images": []}
        texts = [result["reply"]] if result.get("reply") else []
        body = dynamic_block(texts, result.get("images"))
        body.update(
            reply=result.get("reply") or "",
            public_reply=result.get("public_reply") or "",
            car_id=result.get("car_id"),
        )
        return web.json_response(body)

    async def close(self, _app: web.Application | None = None) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def add_routes(self, app: web.Application) -> None:
        app.router.add_post(MANYCHAT_PATH, self.handle)
        app.on_cleanup.append(self.close)
