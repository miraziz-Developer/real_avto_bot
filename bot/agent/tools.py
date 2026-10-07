"""Agent asboblari (tool calling): mashina qidirish, rasm yuborish, mijoz ma'lumoti, menejerga topshirish."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InputMediaPhoto

from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.models import Car, CarStatus, Lead
from bot.db.repositories import CrmRepository
from bot.services.car_cards import channel_post_url
from bot.services.lead_cards import send_lead_card
from bot.services.car_parser import _PHONE_RE
import contextlib

logger = logging.getLogger(__name__)

MAX_PHOTOS = 6
MAX_VIDEOS = 2
MAX_ACTIVE_ALERTS = 5
# channel_watch dumaloq videolarni shu prefiks bilan saqlaydi
VIDEO_NOTE_PREFIX = "vn:"


async def send_car_media(
    bot: Bot,
    chat_id: int,
    car: Car,
    *,
    caption: str | None = None,
    business_connection_id: str | None = None,
) -> int:
    """Mashina rasmlari (albom) va videolari (dumaloq video — dumaloq bo'lib). Yuborilganlar soni."""
    photos = list(car.photo_file_ids or [])[:MAX_PHOTOS]
    videos = list(car.video_file_ids or [])[:MAX_VIDEOS]
    bc = business_connection_id
    sent = 0
    try:
        if len(photos) == 1:
            await bot.send_photo(chat_id, photos[0], caption=caption, parse_mode=None, business_connection_id=bc)
        elif photos:
            # Izoh oddiy matn: mashina nomidagi «&» yoki «<» HTML xatosi bilan butun albomni yiqitmasin
            media = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode=None)] + [
                InputMediaPhoto(media=p) for p in photos[1:]
            ]
            await bot.send_media_group(chat_id, media, business_connection_id=bc)
        sent += len(photos)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("Rasm yuborilmadi (car #%s): %s", car.id, e)
    for v in videos:
        try:
            if v.startswith(VIDEO_NOTE_PREFIX):
                await bot.send_video_note(chat_id, v[len(VIDEO_NOTE_PREFIX):], business_connection_id=bc)
            else:
                await bot.send_video(
                    chat_id, v, caption=None if photos else caption, parse_mode=None, business_connection_id=bc
                )
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("Video yuborilmadi (car #%s): %s", car.id, e)
    if sent and not photos and caption and all(v.startswith(VIDEO_NOTE_PREFIX) for v in videos):
        # Dumaloq videoga izoh qo'yib bo'lmaydi — nomi va narxini alohida yozamiz
        with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
            await bot.send_message(chat_id, caption, business_connection_id=bc, parse_mode=None)
    return sent


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_cars",
            "description": "Avtosalonda hozir sotuvda bor mashinalarni qidirish. Mavjudlik haqida gapirishdan oldin doim chaqir.",
            "parameters": _obj(
                {
                    "model": {"type": "string", "description": "Model nomi, masalan Cobalt, Gentra, Spark, Nexia 3"},
                    "brand": {"type": "string", "description": "Marka, masalan Chevrolet, Kia, BYD"},
                    "year_min": {"type": "integer"},
                    "year_max": {"type": "integer"},
                    "budget_max_usd": {"type": "integer", "description": "Mijozning maksimal byudjeti, dollarda"},
                    "budget_min_usd": {"type": "integer"},
                    "transmission": {"type": "string", "enum": ["avtomat", "mexanika"]},
                    "fuel": {"type": "string", "description": "benzin, metan, propan, elektr, gibrid, dizel"},
                }
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_car_details",
            "description": "Bitta mashina haqida to'liq ma'lumot (id search_cars natijasidan).",
            "parameters": _obj({"car_id": {"type": "integer"}}, ["car_id"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_car_photos",
            "description": "Mashina rasmlari va videolarini (dumaloq video ham) mijozga yuborish.",
            "parameters": _obj({"car_id": {"type": "integer"}}, ["car_id"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_customer_info",
            "description": "Mijoz haqida bilib olingan ma'lumotni saqlash (faqat aniq aytilganini).",
            "parameters": _obj(
                {
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                    "budget_usd": {"type": "integer"},
                    "payment_method": {"type": "string", "description": "naqd, kredit, bo'lib to'lash, trade-in"},
                    "visit_time": {"type": "string", "description": "Qachon ko'rishga keladi, masalan 'bugun 17:00'"},
                    "interested_car_id": {"type": "integer"},
                    "wants": {"type": "string", "description": "Nima qidirayotgani, qisqa"},
                }
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_search_alert",
            "description": "Mos mashina yo'q bo'lsa: shunday mashina kanalga chiqishi bilan mijozga avtomatik xabar.",
            "parameters": _obj(
                {
                    "brand": {"type": "string"},
                    "model": {"type": "string"},
                    "year_min": {"type": "integer"},
                    "year_max": {"type": "integer"},
                    "budget_max_usd": {"type": "integer"},
                },
                ["brand"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_manager",
            "description": "Mijozni jonli menejerga topshirish: sotib olishga/ko'rishga tayyor, narx kelishmoqchi, kredit/hujjat so'rayapti, odam bilan gaplashmoqchi yoki norozi.",
            "parameters": _obj(
                {
                    "reason": {"type": "string", "description": "Nega topshirilyapti, qisqa"},
                    "summary": {"type": "string", "description": "Menejer uchun 1-3 gapli xulosa: kim, nima xohlaydi, nimaga kelishildi"},
                },
                ["reason", "summary"],
            ),
        },
    },
]


def strip_phones(text: str | None) -> str | None:
    """Sotuvchi telefoni mijozga hech qachon berilmaydi — izohdan raqamlarni olib tashlaymiz."""
    if not text:
        return text
    return _PHONE_RE.sub("[raqam yashirilgan]", text)


def car_for_agent(car: Car) -> dict[str, Any]:
    """Agentga beriladigan mashina ma'lumoti — faqat bazadagi faktlar."""
    days = None
    if car.published_at:
        days = (datetime.now(UTC) - car.published_at).days
    d: dict[str, Any] = {
        "id": car.id,
        "title": car.title,
        "status": "bron" if car.status == CarStatus.RESERVED else "sotuvda",
        "year": car.year,
        "price_usd": car.price_usd,
        "mileage_km": car.mileage_km,
        "transmission": car.transmission,
        "fuel": car.fuel,
        "color": car.color,
        "position": car.position,
        "paint": car.paint_status,
        "accident": None if car.has_accident is None else ("bor" if car.has_accident else "yo'q"),
        "location": car.location,
        "notes": strip_phones(car.notes),
        "photos": len(car.photo_file_ids or []),
        "days_on_sale": days,
        "post_url": channel_post_url(car),
    }
    return {k: v for k, v in d.items() if v not in (None, "")}


def _normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        return "+998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    if 10 <= len(digits) <= 15:
        return "+" + digits
    return None


_INT_MAX = 2_147_483_647  # Postgres int — AI juda katta son yuborsa so'rov yiqilmasin


def _int(v: Any) -> int | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError, OverflowError):
        return None
    return n if 0 < n <= _INT_MAX else None


@dataclass
class AgentContext:
    bot: Bot
    chat_id: int
    lead: Lead
    cars: CarRepository
    leads: LeadRepository
    crm: CrmRepository
    business_connection_id: str | None = None  # Telegram Business chati bo'lsa — javoblar egasi nomidan
    # Business chat va Instagram'da inline tugmalar yuborib bo'lmaydi
    buttons_supported: bool = True
    # Telegram'dan boshqa kanal (Instagram) uchun rasm yuborish: (car, file_ids, caption) → yuborilgan soni
    photo_sender: Callable[[Car, list[str], str], Awaitable[int]] | None = None
    shown_car_ids: list[int] = field(default_factory=list)
    handed_off: bool = False

    async def current_car(self) -> Car | None:
        return await self.cars.get(self.lead.car_id) if self.lead.car_id else None

    async def execute(self, name: str, raw_args: str | dict | None) -> str:
        try:
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": "argumentlar JSON emas"})
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return json.dumps({"error": f"noma'lum asbob: {name}"})
        try:
            # Savepoint: asbob ichida DB xatosi bo'lsa faqat shu asbob bekor bo'ladi — sessiya (va suhbat) buzilmaydi
            async with self.cars.session.begin_nested():
                result = await handler(args or {})
        except Exception:
            logger.exception("Agent asbobi xatosi: %s %s", name, args)
            result = {"error": "ichki xato — menejerga topshirishni taklif qil"}
        return json.dumps(result, ensure_ascii=False, default=str)

    async def _tool_search_cars(self, a: dict) -> dict:
        rows = await self.cars.search_offerable(
            model=a.get("model"),
            brand=a.get("brand"),
            year_min=_int(a.get("year_min")),
            year_max=_int(a.get("year_max")),
            price_min_usd=_int(a.get("budget_min_usd")),
            price_max_usd=_int(a.get("budget_max_usd")),
            transmission=a.get("transmission"),
            fuel=a.get("fuel"),
            limit=5,
        )
        self.shown_car_ids.extend(c.id for c in rows)
        if rows:
            return {"cars": [car_for_agent(c) for c in rows]}
        # Aniq mos yo'q — filtrlarni yumshatib o'xshashlarini qaytaramiz
        relaxed = await self.cars.search_offerable(
            model=a.get("model"),
            brand=a.get("brand"),
            price_max_usd=_int(a.get("budget_max_usd")),
            limit=3,
        )
        if not relaxed and _int(a.get("budget_max_usd")):
            relaxed = await self.cars.search_offerable(price_max_usd=_int(a.get("budget_max_usd")), limit=3)
        self.shown_car_ids.extend(c.id for c in relaxed)
        return {
            "cars": [],
            "similar": [car_for_agent(c) for c in relaxed],
            "note": "Aniq mos mashina hozir yo'q. 'similar' — o'xshash variantlar (bo'lsa). Bo'lmasa save_search_alert taklif qil.",
        }

    async def _tool_get_car_details(self, a: dict) -> dict:
        car = await self.cars.get(_int(a.get("car_id")) or 0)
        if car is None:
            return {"error": "bunday mashina yo'q"}
        if car.status not in (CarStatus.ACTIVE, CarStatus.RESERVED):
            similar = await self.cars.similar_offerable(car)
            return {
                "car_id": car.id,
                "status": "sotilgan" if car.status == CarStatus.SOLD else "sotuvda emas",
                "note": "Bu mashina endi sotuvda yo'q. O'xshashlarini taklif qil.",
                "similar": [car_for_agent(c) for c in similar],
            }
        return {"car": car_for_agent(car)}

    async def _tool_send_car_photos(self, a: dict) -> dict:
        car = await self.cars.get(_int(a.get("car_id")) or 0)
        if car is None or car.status not in (CarStatus.ACTIVE, CarStatus.RESERVED):
            return {"error": "mashina sotuvda emas"}
        photos = list(car.photo_file_ids or [])[:MAX_PHOTOS]
        if not photos and not car.video_file_ids:
            url = channel_post_url(car)
            return {"sent": 0, "note": "Bazada rasm/video yo'q." + (f" Kanaldagi post: {url}" if url else "")}
        price = f"${car.price_usd:,}".replace(",", " ") if car.price_usd else ""
        caption = " — ".join(p for p in (car.title, price) if p)
        if self.photo_sender is not None:
            if not photos:
                return {"sent": 0, "note": "Bu kanalda faqat video bor — kanal havolasini ber."}
            sent = await self.photo_sender(car, photos, caption)
            if not sent:
                return {"sent": 0, "note": "Rasm yuborib bo'lmadi — katalog yoki kanal havolasini ber."}
            if not self.lead.car_id:
                self.lead.car_id = car.id
            return {"sent": sent, "note": "Rasmlar yuborildi — ularni qayta tasvirlab o'tirma."}
        sent = await send_car_media(
            self.bot, self.chat_id, car, caption=caption, business_connection_id=self.business_connection_id
        )
        if not sent:
            return {"sent": 0, "error": "rasm/video yuborib bo'lmadi"}
        if not self.lead.car_id:
            self.lead.car_id = car.id
        return {"sent": sent, "note": "Rasm va videolar yuborildi — ularni qayta tasvirlab o'tirma."}

    async def _tool_update_customer_info(self, a: dict) -> dict:
        lead = self.lead
        saved: list[str] = []
        if a.get("name"):
            lead.name = str(a["name"])[:200]
            saved.append("name")
        if a.get("phone"):
            phone = _normalize_phone(str(a["phone"]))
            if phone is None:
                return {"error": "telefon raqami noto'g'ri — qayta so'ra"}
            lead.phone = phone
            if lead.client_id:
                client = await self.crm.get_client_by_id(lead.client_id)
                if client is not None:
                    client.phone = phone
            saved.append("phone")
        if _int(a.get("budget_usd")):
            lead.budget_usd = _int(a.get("budget_usd"))
            saved.append("budget_usd")
        if a.get("payment_method"):
            lead.payment_method = str(a["payment_method"])[:30]
            saved.append("payment_method")
        if a.get("visit_time"):
            lead.visit_time = str(a["visit_time"])[:100]
            saved.append("visit_time")
        if a.get("wants"):
            lead.wants = str(a["wants"])[:500]
            saved.append("wants")
        car_id = _int(a.get("interested_car_id"))
        if car_id:
            car = await self.cars.get(car_id)
            if car is None:
                return {"error": "bunday mashina yo'q", "saved": saved}
            lead.car_id = car.id
            saved.append("interested_car_id")
        return {"saved": saved}

    async def _tool_save_search_alert(self, a: dict) -> dict:
        if not self.lead.client_id:
            return {"error": "mijoz topilmadi"}
        if await self.crm.count_active_wishlists(self.lead.client_id) >= MAX_ACTIVE_ALERTS:
            return {"error": "mijozda allaqachon ko'p faol qidiruv bor"}
        year_now = datetime.now().year
        wish = await self.crm.create_wishlist(
            client_id=self.lead.client_id,
            brand=str(a.get("brand") or "").strip() or "Chevrolet",
            model=(str(a.get("model")).strip() if a.get("model") else None),
            year_min=_int(a.get("year_min")) or 1990,
            year_max=_int(a.get("year_max")) or year_now + 1,
            budget_min=None,
            budget_max=min(_int(a.get("budget_max_usd")) or 1_000_000, 1_000_000),
            condition_key=None,
        )
        return {"saved": True, "alert_id": wish.id, "note": "Mos e'lon kanalga chiqsa mijozga avtomatik xabar boradi."}

    async def _tool_handoff_to_manager(self, a: dict) -> dict:
        lead = self.lead
        reason = str(a.get("reason") or "mijoz tayyor")
        summary = str(a.get("summary") or "")
        first_time = await self.leads.hand_off(lead, reason=reason, summary=summary)
        if not first_time:
            return {"ok": True, "note": "Menejer allaqachon xabardor. Mijozga tez orada bog'lanishini ayt."}
        car = await self.current_car()
        sent = await send_lead_card(self.bot, self.leads, lead, car=car)
        self.handed_off = True
        return {
            "ok": True,
            "notified_managers": sent,
            "note": "Menejerga yuborildi. Mijozga menejer tez orada bog'lanishini ayt (aniq vaqt va'da qilma).",
        }
