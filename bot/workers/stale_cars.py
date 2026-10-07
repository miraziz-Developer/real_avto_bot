"""Uzoq turib qolgan mashinalar: adminlarga «hali sotuvdami?» so'rovi.

Jamoa «sotildi» deb belgilashni unutsa, savdo agenti sotilgan mashinani taklif qilib qo'yadi — shu worker buni oldini oladi.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.services.car_cards import send_car_card_to_admins, stale_prompt_kb

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 6 * 3600
# Tunda adminlarni bezovta qilmaslik (Toshkent vaqti, UTC+5)
_TASHKENT_UTC_OFFSET = 5
_WORK_HOURS_LOCAL = range(9, 22)


def _is_quiet_now() -> bool:
    hour = (datetime.now(UTC).hour + _TASHKENT_UTC_OFFSET) % 24
    return hour not in _WORK_HOURS_LOCAL


async def stale_cars_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    logger.info("Eskirgan mashinalar worker: %s kundan oshganlar tekshiriladi", settings.car_stale_days)
    try:
        await asyncio.sleep(60)  # bot ishga tushishi bilan adminlarga xabar yog'dirmaslik
        while True:
            if settings.admin_telegram_ids and not _is_quiet_now():
                try:
                    await _check_once(bot, session_factory)
                except Exception:
                    logger.exception("Eskirgan mashinalar tekshiruvi xatosi")
                await asyncio.sleep(CHECK_EVERY_SECONDS)
            else:
                await asyncio.sleep(1800)  # tunda yarim soatda bir tekshirib, ish vaqti boshlanishini kutamiz
    except asyncio.CancelledError:
        logger.info("Eskirgan mashinalar worker to'xtatildi")


async def _check_once(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        cars = CarRepository(session)
        rows = await cars.stale_active_cars(older_than_days=settings.car_stale_days, limit=10)
        now = datetime.now(UTC)
        for car in rows:
            days = (now - car.published_at).days if car.published_at else settings.car_stale_days
            await send_car_card_to_admins(
                bot,
                car,
                header=f"⏳ <b>{days} kundan beri sotuvda.</b> Hali sotuvdami?",
                reply_markup=stale_prompt_kb(car),
            )
            await cars.mark_stale_prompted(car)
            await session.commit()
