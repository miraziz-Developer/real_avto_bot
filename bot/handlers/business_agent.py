"""Telegram Business: agent akkaunt egasining (masalan Ikrom aka) shaxsiy chatlarida mijozlarga javob beradi.

Ulash: egasi (Telegram Premium) → Sozlamalar → Telegram Business → Chatbotlar → shu bot, «xabarlarga javob berish» ruxsati.
Muhim: u yerda «Chatlar» bo'limida kontaktlarni (oila, do'stlar) chiqarib tashlang — aks holda AI ularga ham javob beradi.

Qoidalar:
  • egasi mijozga o'zi yozsa — AI shu mijoz bilan BUSINESS_OWNER_PAUSE_HOURS soat jim turadi;
  • botning o'zi yuborgan xabarlar (sender_business_bot) e'tiborsiz qoldiriladi.
"""

from __future__ import annotations

import html
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BusinessConnection, Message

from bot.ai import AIError, get_ai, get_budget
from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.repositories import CrmRepository
from bot.handlers.sales_agent import handle_customer_text
from bot.services.car_cards import notify_admins_text

logger = logging.getLogger(__name__)

router = Router(name="business_agent")


@router.business_connection()
async def on_business_connection(conn: BusinessConnection, bot: Bot, leads: LeadRepository) -> None:
    await leads.upsert_business_connection(
        connection_id=conn.id,
        owner_user_id=conn.user.id,
        owner_name=conn.user.full_name,
        can_reply=conn.can_reply,
        is_enabled=conn.is_enabled,
    )
    await leads.session.commit()
    who = html.escape(conn.user.full_name or str(conn.user.id))
    if conn.is_enabled and conn.can_reply:
        text = f"✅ <b>Telegram Business ulandi</b>: {who}. AI endi shu akkauntga yozgan mijozlarga javob beradi."
    elif conn.is_enabled:
        text = f"⚠️ Business ulandi ({who}), lekin «xabarlarga javob berish» ruxsati berilmagan — AI javob bera olmaydi."
    else:
        text = f"⏸ Telegram Business uzildi: {who}."
    logger.info("Business connection %s: enabled=%s can_reply=%s", conn.id, conn.is_enabled, conn.can_reply)
    await notify_admins_text(bot, text)


@router.business_message()
async def on_business_message(
    message: Message,
    bot: Bot,
    leads: LeadRepository,
    cars: CarRepository,
    crm: CrmRepository,
) -> None:
    if not settings.business_enabled or not message.business_connection_id or message.from_user is None:
        return
    if message.sender_business_bot is not None:
        return  # bot o'zi egasi nomidan yuborgan xabar
    conn = await leads.get_business_connection(message.business_connection_id)
    if conn is None or not conn.is_enabled:
        return

    if message.from_user.id == conn.owner_user_id:
        # Egasi mijozga o'zi yozdi — AI aralashmasin
        lead = await leads.get_open(message.chat.id)
        if lead is not None:
            await leads.pause_for_owner(lead, hours=settings.business_owner_pause_hours)
            await leads.add_message(lead, "admin", message.text or message.caption or "[media]")
            await leads.session.commit()
        return

    if not conn.can_reply:
        return
    text = message.text
    if text is None and (message.voice or message.video_note):
        ai = get_ai()
        media = message.voice or message.video_note
        uid = message.from_user.id if message.from_user else 0
        if ai.enabled and media is not None and await get_budget().allow_user(f"tg:{uid}"):
            try:
                f = await bot.get_file(media.file_id)
                buf = await bot.download_file(f.file_path)
                text = await ai.transcribe(
                    buf.read() if buf else b"",
                    filename="audio.ogg" if message.voice else "video.mp4",
                )
            except (AIError, TelegramBadRequest) as e:
                logger.warning("Business ovozini o'qib bo'lmadi: %s", e)
    if not text or text.startswith("/"):
        return  # rasm/stiker va h.k. — egasi o'zi ko'radi
    await handle_customer_text(
        message,
        bot,
        text,
        leads=leads,
        cars=cars,
        crm=crm,
        business_connection_id=conn.id,
    )
