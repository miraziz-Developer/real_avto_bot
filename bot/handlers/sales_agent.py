"""Mijoz tomoni: botga yozilgan har qanday savolga AI savdo agenti javob beradi.

Bu router ENG OXIRIDA ulanadi — e'lon berish, qidiruv saqlash va boshqa FSM oqimlari ustun turadi.
Admin «Oldim» bosgan bo'lsa (human_mode) AI jim turadi va mijoz xabarlari menejerga uzatiladi.
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.types import (
    CallbackQuery,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)

from bot.agent.fallback import HANDOFF_CB, fallback_reply
from bot.agent.runner import run_agent
from bot.agent.tools import AgentContext, _normalize_phone, car_for_agent
from bot.ai import AIError, get_ai
from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.models import CarStatus, Lead
from bot.db.repositories import CrmRepository
from bot.services.lead_cards import lead_score, send_lead_card

logger = logging.getLogger(__name__)

router = Router(name="sales_agent")
router.message.filter(F.chat.type == "private")

AGENT_START_CB = "agent_start"
_TG_LIMIT = 4000

_locks: dict[int, asyncio.Lock] = {}


def _user_lock(uid: int) -> asyncio.Lock:
    """Bir mijozning ketma-ket xabarlari navbat bilan (parallel AI javoblar aralashmasin)."""
    lock = _locks.get(uid)
    if lock is None:
        if len(_locks) > 5000:
            _locks.clear()
        lock = _locks[uid] = asyncio.Lock()
    return lock


def _full_name(u: User) -> str | None:
    return " ".join(p for p in (u.first_name, u.last_name) if p) or None


async def open_lead(user: User, leads: LeadRepository, crm: CrmRepository) -> Lead:
    client = await crm.get_or_create_client(telegram_id=user.id, full_name=_full_name(user))
    lead, _ = await leads.get_or_create_open(
        user.id, name=_full_name(user), username=user.username, client_id=client.id
    )
    return lead


def _chunks(text: str) -> list[str]:
    return [text[i : i + _TG_LIMIT] for i in range(0, len(text), _TG_LIMIT)] or [""]


async def _forward_to_manager(bot: Bot, leads: LeadRepository, lead: Lead, message: Message, text: str | None) -> None:
    """Odam rejimida mijoz xabarini menejer(lar)ga uzatish — menejer reply qilsa javob mijozga boradi."""
    targets = [lead.assigned_admin_id] if lead.assigned_admin_id else list(settings.admin_telegram_ids)
    name = html.escape(lead.name or "Mijoz")
    header = f"💬 <b>{name}</b> (lead #{lead.id})"
    for aid in targets:
        try:
            if text and not (message.photo or message.video or message.document):
                m = await bot.send_message(aid, f"{header}:\n{html.escape(text)}", parse_mode=ParseMode.HTML)
                await leads.add_relay(lead, aid, m.message_id)
            else:
                h = await bot.send_message(aid, header + ":", parse_mode=ParseMode.HTML)
                await leads.add_relay(lead, aid, h.message_id)
                c = await bot.copy_message(aid, from_chat_id=message.chat.id, message_id=message.message_id)
                await leads.add_relay(lead, aid, c.message_id)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("Lead #%s xabari admin %s ga uzatilmadi: %s", lead.id, aid, e)


async def handle_customer_text(
    message: Message,
    bot: Bot,
    text: str,
    *,
    leads: LeadRepository,
    cars: CarRepository,
    crm: CrmRepository,
) -> None:
    user = message.from_user
    if user is None:
        return
    async with _user_lock(user.id):
        lead = await open_lead(user, leads, crm)
        await leads.add_message(lead, "user", text)

        if lead.human_mode:
            await _forward_to_manager(bot, leads, lead, message, text)
            await leads.session.commit()
            return

        ctx = AgentContext(bot=bot, chat_id=user.id, lead=lead, cars=cars, leads=leads, crm=crm)
        try:
            await bot.send_chat_action(user.id, ChatAction.TYPING)
        except TelegramBadRequest:
            pass
        kb = None
        ai = get_ai()
        if ai.enabled:
            try:
                reply = await run_agent(ctx, ai, model=settings.agent_model)
            except AIError as e:
                logger.warning("AI agent ishlamadi (lead #%s), oddiy javob: %s", lead.id, e)
                reply, kb = await fallback_reply(ctx, text)
        else:
            reply, kb = await fallback_reply(ctx, text)

        await leads.add_message(lead, "assistant", reply)
        lead.score = lead_score(lead)
        await leads.session.commit()

    parts = _chunks(reply)
    for i, part in enumerate(parts):
        await message.answer(
            part,
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup=kb if i == len(parts) - 1 else None,
        )


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_customer_text(
    message: Message, bot: Bot, leads: LeadRepository, cars: CarRepository, crm: CrmRepository
) -> None:
    if not settings.agent_enabled:
        return
    await handle_customer_text(message, bot, message.text or "", leads=leads, cars=cars, crm=crm)


@router.message(StateFilter(None), F.voice | F.video_note)
async def on_customer_voice(
    message: Message, bot: Bot, leads: LeadRepository, cars: CarRepository, crm: CrmRepository
) -> None:
    if not settings.agent_enabled or message.from_user is None:
        return
    lead = await leads.get_open(message.from_user.id)
    if lead is not None and lead.human_mode:
        await leads.add_message(lead, "user", "[ovozli xabar]")
        await _forward_to_manager(bot, leads, lead, message, None)
        return
    ai = get_ai()
    media = message.voice or message.video_note
    if not ai.enabled or media is None:
        await message.answer("Iltimos, savolingizni matn bilan yozing 🙏", parse_mode=None)
        return
    try:
        f = await bot.get_file(media.file_id)
        buf = await bot.download_file(f.file_path)
        text = await ai.transcribe(buf.read() if buf else b"", filename="audio.ogg" if message.voice else "video.mp4")
    except (AIError, TelegramBadRequest) as e:
        logger.warning("Mijoz ovozini o'qib bo'lmadi: %s", e)
        text = ""
    if not text:
        await message.answer("Ovozni tushuna olmadim, matn bilan yozib yuborasizmi? 🙏", parse_mode=None)
        return
    await handle_customer_text(message, bot, text, leads=leads, cars=cars, crm=crm)


@router.message(StateFilter(None), F.photo | F.video | F.document)
async def on_customer_media(message: Message, bot: Bot, leads: LeadRepository, crm: CrmRepository) -> None:
    if not settings.agent_enabled or message.from_user is None:
        return
    lead = await open_lead(message.from_user, leads, crm)
    note = message.caption or "[rasm/fayl]"
    await leads.add_message(lead, "user", note)
    # AI rasmni ko'rmaydi — menejerga uzatamiz (masalan trade-in uchun mijoz o'z mashinasi rasmini yuborgan)
    await _forward_to_manager(bot, leads, lead, message, None)
    if not lead.human_mode:
        await message.answer(
            "Rasm uchun rahmat! Menejerimiz ko'rib chiqadi. Savolingiz bo'lsa yozing 🙂", parse_mode=None
        )


@router.message(StateFilter(None), F.contact)
async def on_customer_contact(message: Message, bot: Bot, leads: LeadRepository, crm: CrmRepository) -> None:
    if message.from_user is None or message.contact is None:
        return
    lead = await open_lead(message.from_user, leads, crm)
    phone = _normalize_phone(message.contact.phone_number) or message.contact.phone_number
    lead.phone = phone
    client = await crm.get_or_create_client(telegram_id=message.from_user.id, phone=phone)
    lead.client_id = client.id
    lead.score = lead_score(lead)
    await leads.add_message(lead, "user", f"[telefon qoldirdi: {phone}]")
    targets = [lead.assigned_admin_id] if lead.assigned_admin_id else list(settings.admin_telegram_ids)
    for aid in targets:
        try:
            m = await bot.send_message(
                aid,
                f"📞 <b>{html.escape(lead.name or 'Mijoz')}</b> (lead #{lead.id}) raqam qoldirdi: "
                f"<code>{html.escape(phone)}</code>",
                parse_mode=ParseMode.HTML,
            )
            await leads.add_relay(lead, aid, m.message_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
    await message.answer(
        "Rahmat! Menejerimiz tez orada qo'ng'iroq qiladi ✅", reply_markup=ReplyKeyboardRemove(), parse_mode=None
    )


def _phone_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Raqamimni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@router.callback_query(F.data == HANDOFF_CB)
async def on_handoff_button(
    cq: CallbackQuery, bot: Bot, leads: LeadRepository, cars: CarRepository, crm: CrmRepository
) -> None:
    if cq.from_user is None:
        return
    lead = await open_lead(cq.from_user, leads, crm)
    first = await leads.hand_off(lead, reason="Mijoz «Menejer bilan bog'lanish» tugmasini bosdi", summary=None)
    if first:
        car = await cars.get(lead.car_id) if lead.car_id else None
        await send_lead_card(bot, leads, lead, car=car)
    await leads.session.commit()
    await cq.answer()
    if cq.message:
        text = "✅ Menejerga xabar berdik, tez orada bog'lanadi."
        if not lead.phone:
            text += "\nQo'ng'iroq qilishimiz uchun raqamingizni yuborasizmi?"
        await cq.message.answer(text, reply_markup=None if lead.phone else _phone_request_kb(), parse_mode=None)


@router.callback_query(F.data == AGENT_START_CB)
async def on_agent_start(cq: CallbackQuery) -> None:
    await cq.answer()
    if cq.message:
        await cq.message.answer(
            "Qanday mashina qidiryapsiz? 🚗\nModel, yil va byudjetingizni yozing — masalan: "
            "«Cobalt, 2019 dan yangi, 10 000$ gacha». Ovozli xabar ham bo'ladi.",
            parse_mode=None,
        )


def _price(n: int | None) -> str:
    return f"${n:,}".replace(",", " ") if n else ""


async def open_car_entry(
    message: Message,
    car_id: int,
    *,
    leads: LeadRepository,
    cars: CarRepository,
    crm: CrmRepository,
) -> None:
    """Deep-link `car_<id>` (wishlist xabari va h.k.): mashina ma'lumoti + rasmlar, suhbat shu mashina bilan boshlanadi."""
    user = message.from_user
    if user is None:
        return
    car = await cars.get(car_id)
    lead = await open_lead(user, leads, crm)
    if car is None or car.status not in (CarStatus.ACTIVE, CarStatus.RESERVED):
        similar = await cars.similar_offerable(car) if car is not None else []
        lines = ["Afsuski, bu mashina endi sotuvda yo'q."]
        if similar:
            lines.append("O'xshash variantlar:")
            lines.extend(f"• {c.title} {_price(c.price_usd)}".rstrip() for c in similar)
        lines.append("Qanday mashina qidiryapsiz? Yozing, yordam beraman.")
        text = "\n".join(lines)
        await leads.add_message(lead, "assistant", text)
        await message.answer(text, parse_mode=None)
        return
    lead.car_id = car.id
    d = car_for_agent(car)
    parts = [f"🚗 {car.title}"]
    if car.price_usd:
        parts.append(f"💰 {_price(car.price_usd)}")
    if car.mileage_km is not None:
        parts.append(f"🛣 {car.mileage_km:,} km".replace(",", " "))
    specs = " · ".join(str(d[k]) for k in ("transmission", "fuel", "color", "position") if k in d)
    if specs:
        parts.append(f"⚙️ {specs}")
    if car.paint_status:
        parts.append(f"🖌 Kraska: {car.paint_status}")
    if car.status == CarStatus.RESERVED:
        parts.append("🔵 Hozir bron qilingan")
    parts.append("")
    parts.append("Savolingiz bo'lsa yozing — yoki ko'rishga qachon kela olasiz? 🙂")
    text = "\n".join(parts)
    photos = list(car.photo_file_ids or [])[:6]
    try:
        if len(photos) > 1:
            await message.answer_media_group([InputMediaPhoto(media=p) for p in photos])
        elif photos:
            await message.answer_photo(photos[0])
    except TelegramBadRequest as e:
        logger.warning("Mashina #%s rasmlari yuborilmadi: %s", car.id, e)
    await leads.add_message(lead, "assistant", text)
    lead.score = lead_score(lead)
    await message.answer(text, parse_mode=None)
