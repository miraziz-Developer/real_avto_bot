"""Har kuni kechqurun adminlarga qisqa hisobot: sotuvdagi mashinalar, bugungi yangi/sotilgan, mijozlar."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import AppMeta, Car, CarStatus, Lead, LeadStatus
from bot.services.car_cards import notify_admins_text

logger = logging.getLogger(__name__)

META_KEY = "daily_report_date"
_TASHKENT = timedelta(hours=5)


async def build_report(session: AsyncSession, now: datetime) -> str:
    cars = CarRepository(session)
    since = now - timedelta(days=1)
    s = await cars.stats(days=1)
    st = s["by_status"]
    new_leads = (await session.execute(select(func.count()).select_from(Lead).where(Lead.created_at >= since))).scalar_one()
    waiting = (
        await session.execute(select(func.count()).select_from(Lead).where(Lead.status == LeadStatus.HANDED_OFF))
    ).scalar_one()
    sold_today = (
        await session.execute(
            select(Car).where(Car.status == CarStatus.SOLD, Car.sold_at >= since).order_by(Car.sold_at.desc()).limit(8)
        )
    ).scalars().all()
    local = now + _TASHKENT
    lines = [
        f"📅 <b>Kunlik hisobot</b> — {local:%d.%m.%Y}",
        "",
        f"🟢 Sotuvda: <b>{st.get(CarStatus.ACTIVE, 0)}</b> ta" + (f" (jami ${s['active_value_usd']:,})" if s["active_value_usd"] else ""),
        f"🆕 Bugun qo'shildi: {s['new_count']} · 🔴 Sotildi: <b>{s['sold_count']}</b>",
    ]
    if st.get(CarStatus.REVIEW):
        lines.append(f"🟡 Tekshiruvda (ma'lumoti to'liq emas): {st[CarStatus.REVIEW]} ta")
    lines.append(f"👥 Yangi mijozlar: {new_leads}" + (f" · ⏳ Javob kutyapti: <b>{waiting}</b>" if waiting else ""))
    if sold_today:
        lines.append("")
        lines.append("🔴 Bugun sotilganlar:")
        lines.extend(f"• <code>#{c.id}</code> {html.escape(c.title)}" for c in sold_today)
    if settings.crm_url:
        lines.append("")
        lines.append(f'📊 <a href="{html.escape(settings.crm_url)}">CRM ni ochish</a>')
    return "\n".join(lines)


async def _sent_on(session_factory: async_sessionmaker[AsyncSession]) -> str | None:
    async with session_factory() as session:
        row = await session.get(AppMeta, META_KEY)
    return row.value if row else None


async def _mark_sent(session_factory: async_sessionmaker[AsyncSession], day: str) -> None:
    async with session_factory() as session:
        row = await session.get(AppMeta, META_KEY)
        if row is None:
            session.add(AppMeta(key=META_KEY, value=day))
        else:
            row.value = day
        await session.commit()


async def daily_report_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    hour = settings.daily_report_hour
    if hour < 0 or hour > 23 or not settings.admin_telegram_ids:
        logger.info("Kunlik hisobot o'chiq")
        return
    logger.info("Kunlik hisobot: har kuni soat %s:00 (Toshkent)", hour)
    while True:
        try:
            now = datetime.now(UTC)
            local = now + _TASHKENT
            day = local.date().isoformat()
            if local.hour >= hour and await _sent_on(session_factory) != day:
                async with session_factory() as session:
                    text = await build_report(session, now)
                await notify_admins_text(bot, text)
                await _mark_sent(session_factory, day)
                logger.info("Kunlik hisobot yuborildi (%s)", day)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Kunlik hisobot xatosi")
        await asyncio.sleep(600)
