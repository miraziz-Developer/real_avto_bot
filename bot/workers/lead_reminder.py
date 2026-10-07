"""Adminga topshirilgan, lekin hech kim «Oldim» bosmagan mijozlar uchun qayta eslatma — issiq mijoz sovib qolmasin."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.services.lead_cards import send_lead_card

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 60


async def lead_reminder_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    logger.info("Lead eslatma worker: %s daqiqada olinmagan mijozlar uchun", settings.lead_reminder_minutes)
    try:
        while True:
            if settings.admin_telegram_ids:
                try:
                    await remind_once(bot, session_factory)
                except Exception:
                    logger.exception("Lead eslatma xatosi")
            await asyncio.sleep(CHECK_EVERY_SECONDS)
    except asyncio.CancelledError:
        logger.info("Lead eslatma worker to'xtatildi")


async def remind_once(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        leads = LeadRepository(session)
        cars = CarRepository(session)
        rows = await leads.due_for_reminder(after_minutes=settings.lead_reminder_minutes)
        now = datetime.now(UTC)
        for lead in rows:
            minutes = int((now - lead.handed_off_at).total_seconds() // 60) if lead.handed_off_at else 0
            car = await cars.get(lead.car_id) if lead.car_id else None
            await send_lead_card(
                bot,
                leads,
                lead,
                car=car,
                header=f"⏰ <b>{minutes} daqiqadan beri hech kim olmadi!</b> Mijoz kutmoqda",
            )
            lead.reminded_at = now
            await session.commit()
        return len(rows)
