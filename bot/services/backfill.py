"""Yangilanishdan oldin tasdiqlangan bot e'lonlarini mashinalar bazasiga ko'chirish (bir martalik, idempotent).

AI agent va katalog faqat `cars` jadvalidan ishlaydi. Eski versiyada tasdiqlangan e'lonlar u yerda yo'q edi —
ularsiz agent sotuvdagi mashinalarni «ko'rmaydi». Har ishga tushishda chaqiriladi, lekin faqat hali
ko'chirilmaganlarini oladi (listing_submission_id bo'yicha).
"""

from __future__ import annotations

import logging

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.cars_repo import CarRepository
from bot.db.models import Car, ListingSubmission, ListingSubmissionStatus
from bot.services.car_cards import car_from_approved_listing

logger = logging.getLogger(__name__)

# Sotuvchi «sotildi» demagan (sotuvda yoki «sotilmadi») e'lonlar
_ON_SALE = ("open", "feedback_pending", "not_sold")


async def backfill_listing_cars(
    session_factory: async_sessionmaker[AsyncSession], *, channel_chat_id: int | None, limit: int = 1000
) -> int:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ListingSubmission)
                .where(
                    ListingSubmission.status == ListingSubmissionStatus.APPROVED,
                    ListingSubmission.sale_status.in_(_ON_SALE),
                    ~exists(select(Car.id).where(Car.listing_submission_id == ListingSubmission.id)),
                )
                .order_by(ListingSubmission.id)
                .limit(limit)
            )
        ).scalars().all()
        if not rows:
            return 0
        cars = CarRepository(session)
        for sub in rows:
            await car_from_approved_listing(
                cars,
                sub,
                channel_chat_id=channel_chat_id if sub.channel_message_id else None,
                channel_message_ids=[int(sub.channel_message_id)] if sub.channel_message_id else [],
            )
        await session.commit()
    logger.info("Eski tasdiqlangan e'lonlardan %s ta mashina bazaga qo'shildi", len(rows))
    return len(rows)
