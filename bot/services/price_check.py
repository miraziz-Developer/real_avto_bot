"""Narx sog'lomligi: e'londan olingan narx haqiqiy narxga o'xshaydimi.

Kanalda nasiya boshlang'ich to'lovi («1000 dollar pulingiz bo'lsa»), oylik to'lov yoki boshqa mashinaning narxi
narx deb olinib qolishi mumkin. Shubhali narxli mashina sotuvga o'zicha chiqmaydi — admin tasdiqlaydi.
"""

from __future__ import annotations

import re
import statistics

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Car

# Shundan arzon narx 2000-yildan keyingi mashina uchun deyarli har doim boshlang'ich/oylik to'lov
MIN_PLAUSIBLE_USD = 1200
OLD_CAR_YEAR = 2000
# Bazadagi shu model (±2 yil) narxlari medianasidan shuncha marta past/baland bo'lsa — shubhali
LOW_RATIO = 0.45
HIGH_RATIO = 2.5
MIN_SAMPLES = 3

_NASIYA_RE = re.compile(r"nasiya|boshlang|oldindan|oyiga|bo'lib to'la|первоначал|взнос", re.IGNORECASE)


def price_problem(price: int | None, year: int | None, notes: str | None = None) -> str | None:
    """Faqat e'lonning o'zidan ko'rinadigan muammo (bazasiz)."""
    if not price:
        return None
    if price < MIN_PLAUSIBLE_USD and (year is None or year >= OLD_CAR_YEAR):
        return f"${price} — bu mashina uchun juda arzon (nasiya boshlang'ich/oylik to'lov bo'lishi mumkin)"
    if notes and _NASIYA_RE.search(notes):
        nums = {int(re.sub(r"[ ,.]", "", n)) for n in re.findall(r"\d{1,3}(?:[ ,.]\d{3})+|\d+", notes)}
        if price in nums:
            return f"${price} — izohda nasiya to'lovi sifatida aytilgan"
    return None


async def market_problem(session: AsyncSession, model: str | None, year: int | None, price: int | None, *,
                         exclude_id: int | None = None) -> str | None:
    """Bazadagi shu model narxlari bilan solishtirish (kamida 3 ta narxli yozuv bo'lsa)."""
    if not (model and price):
        return None
    stmt = select(Car.price_usd).where(Car.price_usd.is_not(None), Car.model.ilike(model.split()[0]))
    if year:
        stmt = stmt.where(Car.year.between(year - 2, year + 2))
    if exclude_id is not None:
        stmt = stmt.where(Car.id != exclude_id)
    prices = [p for p in (await session.execute(stmt.limit(200))).scalars().all() if p]
    if len(prices) < MIN_SAMPLES:
        return None
    median = statistics.median(prices)
    if price < median * LOW_RATIO:
        return f"${price} — bazadagi shunday mashinalardan ancha arzon (odatda ~${int(median):,})".replace(",", " ")
    if price > median * HIGH_RATIO:
        return f"${price} — bazadagi shunday mashinalardan ancha qimmat (odatda ~${int(median):,})".replace(",", " ")
    return None


async def car_price_problem(session: AsyncSession, car: Car) -> str | None:
    return price_problem(car.price_usd, car.year, car.notes) or await market_problem(
        session, car.model, car.year, car.price_usd, exclude_id=car.id
    )
