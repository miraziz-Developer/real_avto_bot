"""Muzlatish tugagan e'lonlarni avtomatik kanalga chiqarish (faqat ish vaqtida).

Jamoa e'lonni muzlatish muddatida hal qilmasa (tasdiq / rad / sotib olish), yoki sotuvchi sotib olish
taklifiga BUYOUT_REPLY_HOURS ichida javob bermasa — e'lon sotuvchi xohlagandek kanalga chiqadi.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.repositories import CrmRepository
from bot.services.car_cards import notify_admins_text
from bot.services.listing_publish import PublishError, publish_listing
from bot.services.work_hours import is_work_time

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 60
RETRY_AFTER_ERROR = timedelta(hours=1)


async def listing_freeze_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    if settings.listing_freeze_hours <= 0:
        logger.info("E'lon muzlatish o'chirilgan (LISTING_FREEZE_HOURS=0) — avtomatik joylash yo'q")
        return
    logger.info("E'lon muzlatish worker: %s ish soati", settings.listing_freeze_hours)
    try:
        while True:
            try:
                await publish_due_once(bot, session_factory)
            except Exception:
                logger.exception("Avtomatik joylash xatosi")
            await asyncio.sleep(CHECK_EVERY_SECONDS)
    except asyncio.CancelledError:
        logger.info("E'lon muzlatish worker to'xtatildi")


async def publish_due_once(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    # Tunda kanalga e'lon chiqarmaymiz — ertalab ish vaqti boshlanganda chiqadi
    if not is_work_time(now, start_hour=settings.work_hour_start, end_hour=settings.work_hour_end):
        return 0
    published = 0
    async with session_factory() as session:
        crm = CrmRepository(session)
        cars = CarRepository(session)
        for sub in await crm.listings_due_for_auto_publish(now=now):
            offer_expired = sub.buyout_status == "offered"
            if offer_expired:
                sub.buyout_status = "expired"
            try:
                msgs = await publish_listing(bot, crm, cars, sub, auto=True)
            except PublishError as e:
                sub.frozen_until = now + RETRY_AFTER_ERROR
                await session.commit()
                await notify_admins_text(bot, f"⚠️ E'lon #{sub.id} avtomatik joylanmadi:\n{e.html_text}")
                continue
            await session.commit()
            if msgs is None:
                continue
            published += 1
            why = "sotuvchi sotib olish taklifiga javob bermadi" if offer_expired else "muzlatish muddati tugadi"
            await notify_admins_text(
                bot,
                f"⏱ E'lon <b>#{sub.id}</b> {html.escape(f'{sub.brand} {sub.model} {sub.year}')} "
                f"avtomatik kanalga chiqdi ({why}).",
            )
    return published
