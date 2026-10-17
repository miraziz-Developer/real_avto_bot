"""Kanaldan o'chirilgan postlarni aniqlash.

Telegram kanal postlari o'chirilganini botga xabar qilmaydi. Jamoa sotilgan mashina postini shunchaki o'chirib
yuborsa, mashina bazada «sotuvda» qolib, agent uni taklif qilaverardi. Shu worker har bir necha soatda
tekshiradi: postga «tugmani olib tashlash» (editMessageReplyMarkup) so'rovi yuboriladi —
  • post bor (boshqa admin yozgan)  → Telegram «message can't be edited» deydi, post O'ZGARMAYDI;
  • post bor (bot o'zi yozgan albom) → «message is not modified» (albomda tugma bo'lmaydi);
  • post o'chirilgan               → «message to edit not found».
Faqat oxirgi javob (mashinaning BARCHA xabarlari uchun) o'chirilgan deb hisoblanadi; boshqa har qanday xato — noma'lum,
hech narsa o'zgartirilmaydi. Tugmali postlar (boshqa botlar orqali) tekshirilmaydi — tugmasi o'chib ketmasligi uchun.
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarStatus
from bot.services.car_cards import notify_admins_text

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 3 * 3600
FIRST_CHECK_AFTER_SECONDS = 120
PROBE_PAUSE_SECONDS = 0.35
# Bir tekshiruvda haddan tashqari ko'p «o'chirilgan» chiqsa — nimadir noto'g'ri (kanal ID o'zgargan va h.k.):
# hech narsani o'zgartirmaymiz, adminlarga xabar beramiz
MASS_DELETE_GUARD = 0.5
MASS_DELETE_MIN = 5
HAS_MARKUP_EVENT = "post_has_markup"

EXISTS, DELETED, UNKNOWN = "exists", "deleted", "unknown"


async def probe_message(bot: Bot, chat_id: int, message_id: int) -> str:
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        return EXISTS  # tahrir o'tdi (bot o'zi yozgan, tugmasiz post) — post bor
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await probe_message(bot, chat_id, message_id)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message to edit not found" in msg:
            return DELETED
        if "can't be edited" in msg or "not modified" in msg:
            return EXISTS
        logger.info("Post tekshiruvi noma'lum javob (chat %s msg %s): %s", chat_id, message_id, e)
        return UNKNOWN
    except Exception as e:
        logger.warning("Post tekshiruvi xatosi (chat %s msg %s): %s", chat_id, message_id, e)
        return UNKNOWN


async def car_post_state(bot: Bot, car: Car) -> str:
    """Mashinaning birorta xabari bor bo'lsa — EXISTS; hammasi aniq o'chirilgan bo'lsa — DELETED."""
    saw_unknown = False
    for mid in car.channel_message_ids:
        state = await probe_message(bot, int(car.channel_chat_id), int(mid))
        await asyncio.sleep(PROBE_PAUSE_SECONDS)
        if state == EXISTS:
            return EXISTS
        if state == UNKNOWN:
            saw_unknown = True
    return UNKNOWN if saw_unknown else DELETED


def deleted_post_kb(car_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Sotildi", callback_data=f"car:sold:{car_id}"),
                InlineKeyboardButton(text="↩️ Hali sotuvda", callback_data=f"car:ok:{car_id}"),
            ]
        ]
    )


async def check_deleted_posts_once(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        cars = CarRepository(session)
        rows = [c for c in await cars.cars_with_live_posts() if not await cars.has_event(c, HAS_MARKUP_EVENT)]
        await session.commit()
    deleted: list[int] = []
    for car in rows:
        if await car_post_state(bot, car) == DELETED:
            deleted.append(car.id)
    if not deleted:
        return 0
    if len(deleted) >= MASS_DELETE_MIN and len(deleted) > len(rows) * MASS_DELETE_GUARD:
        logger.error("Post tekshiruvi: %s/%s post o'chirilgan ko'rindi — o'zgartirilmadi", len(deleted), len(rows))
        await notify_admins_text(
            bot,
            f"⚠️ Kanal tekshiruvi: {len(deleted)} ta / {len(rows)} ta post topilmadi. Bu juda ko'p — ehtimol "
            "kanal o'zgargan yoki bot adminlikdan chiqarilgan. Hech narsa o'zgartirilmadi, <code>CHANNEL_ID</code> "
            "va bot huquqlarini tekshiring.",
        )
        return 0
    async with session_factory() as session:
        cars = CarRepository(session)
        for car_id in deleted:
            car = await cars.get(car_id)
            if car is None or car.status not in (CarStatus.ACTIVE, CarStatus.RESERVED, CarStatus.REVIEW):
                continue
            await cars.add_event(car, "post_deleted", {"message_ids": list(car.channel_message_ids)})
            await cars.set_status(car, CarStatus.ARCHIVED)
            await session.commit()
            logger.info("Mashina #%s: kanal posti o'chirilgan — arxivga olindi", car.id)
            for aid in settings.admin_telegram_ids:
                try:
                    await bot.send_message(
                        aid,
                        f"🗑 <b>Kanaldagi post o'chirilgan</b>: {html.escape(car.title)} <code>#{car.id}</code>\n"
                        "Mashina sotuvdan olindi (agent endi taklif qilmaydi). Sotildimi?",
                        parse_mode="HTML",
                        reply_markup=deleted_post_kb(car.id),
                    )
                except Exception as e:
                    logger.warning("Admin %s ga o'chirilgan post xabari yuborilmadi: %s", aid, e)
    return len(deleted)


async def post_watch_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    logger.info("Kanal postlari kuzatuvi: har %s soatda o'chirilgan postlar tekshiriladi", CHECK_EVERY_SECONDS // 3600)
    try:
        await asyncio.sleep(FIRST_CHECK_AFTER_SECONDS)
        while True:
            try:
                n = await check_deleted_posts_once(bot, session_factory)
                if n:
                    logger.info("Kanal postlari kuzatuvi: %s ta o'chirilgan post topildi", n)
            except Exception:
                logger.exception("Kanal postlari kuzatuvi xatosi")
            await asyncio.sleep(CHECK_EVERY_SECONDS)
    except asyncio.CancelledError:
        logger.info("Kanal postlari kuzatuvi to'xtatildi")
        raise
