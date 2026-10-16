"""Bir martalik ma'lumot tuzatishlari — bot ishga tushganda (har biri AppMeta kaliti bilan bir marta).

2026-10 tahlili (2 kunlik ish): kunlik obzor videosi + «BUGUN NASIYA SAVDOSI BOR» banneri mashina bo'lib qolgan,
nasiya boshlang'ich to'lovi narx deb olingan, e'londan keyingi video alohida mashina bo'lib qolgan, «Daewoo Matiz»
qayta joylanganda «Chevrolet Matiz» bilan birlashmagan. Kod tuzatildi; bu yerda bazadagi oqibatlari tuzatiladi va
adminlarga nima o'zgargani yuboriladi.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository, _brand_aliases, same_car_model
from bot.db.models import AppMeta, Car, CarSource, CarStatus
from bot.services.car_cards import notify_admins_text
from bot.services.car_extract import _price_is_down_payment
from bot.services.car_parser import down_payment_amounts, parse_car_text

logger = logging.getLogger(__name__)

# v2: narx matni normalizatsiyadan keyin o'qiladi («bo’lsa» egri apostrof bilan ham)
FIX_KEY = "fix:2026-10-analysis-v3"  # v3: AI izohida boshlang'ich to'lov deb yozilgan summa ham
_OPEN = (CarStatus.ACTIVE, CarStatus.REVIEW, CarStatus.RESERVED)
_NO_INFO_PREFIXES = ("postda mashina haqida ma'lumot yo'q",)
# E'londan keyin shuncha vaqt ichida yaratilgan, o'zida ma'lumoti yo'q «mashina» — o'sha e'lonning videosi
_FOLLOWUP_SECONDS = 180
RECENT = timedelta(days=10)


def _no_info(car: Car) -> bool:
    return (car.notes or "").strip().lower().startswith(_NO_INFO_PREFIXES)


def _same_car(a: Car, b: Car) -> bool:
    return bool(
        a.model and b.model and same_car_model(a.model, b.model)
        and a.year and a.year == b.year
        and a.mileage_km and a.mileage_km == b.mileage_km
        and (not a.brand or not b.brand or b.brand.lower() in _brand_aliases(a.brand))
    )


async def fix_cars(session: AsyncSession, now: datetime | None = None) -> list[str]:
    """Tuzatishlar ro'yxati (admin xabari uchun qatorlar)."""
    now = now or datetime.now(UTC)
    cars = CarRepository(session)
    rows = (
        await session.execute(
            select(Car)
            .where(Car.source == CarSource.CHANNEL, Car.status.in_(_OPEN), Car.created_at >= now - RECENT)
            .order_by(Car.id)
        )
    ).scalars().all()
    lines: list[str] = []
    gone: set[int] = set()

    for car in rows:
        if _no_info(car):
            await cars.add_event(car, "auto_fix", {"reason": "not_listing"})
            await cars.set_status(car, CarStatus.ARCHIVED)
            gone.add(car.id)
            lines.append(f"🗄 <code>#{car.id}</code> {html.escape(car.title)} — e'lon emas edi (kunlik obzor/banner), arxivga")

    for car in rows:
        if car.id in gone or not car.price_usd:
            continue
        in_text = car.price_usd in down_payment_amounts(car.raw_text or "")
        in_notes = _price_is_down_payment(car.price_usd, car.notes)
        if in_text or in_notes:
            new = parse_car_text(car.raw_text or "", usd_rate_uzs=settings.usd_rate_uzs).price_usd
            if new == car.price_usd:
                if not in_notes:
                    continue
                new = None  # AI shu summani boshlang'ich to'lov degan — boshqa joyda ham yozilgan bo'lsa ham narx emas
            await cars.add_event(car, "price_changed", {"old": car.price_usd, "new": new, "reason": "nasiya_down_payment"})
            old = car.price_usd
            car.price_usd = new
            shown = f"${new:,}".replace(",", " ") if new else "aytilmagan"
            lines.append(
                f"💰 <code>#{car.id}</code> {html.escape(car.title)} — ${old} nasiya boshlang'ich to'lovi edi, narx emas → {shown}"
            )

    alive = [c for c in rows if c.id not in gone]
    for i, later in enumerate(alive):
        if later.id in gone or i == 0:
            continue
        earlier = [c for c in alive[:i] if c.id not in gone]
        target, why = None, ""
        bare = later.status == CarStatus.REVIEW and not (later.year or later.mileage_km or later.price_usd)
        prev = earlier[-1] if earlier else None
        if (
            bare and prev is not None and later.created_at is not None and prev.created_at is not None
            and 0 <= (later.created_at - prev.created_at).total_seconds() <= _FOLLOWUP_SECONDS
        ):
            target, why = prev, "shu e'lonning videosi edi"
        else:
            target = next((c for c in earlier if _same_car(c, later)), None)
            why = "qayta joylangan o'sha mashina"
        if target is None:
            continue
        await cars.merge_duplicate(later, target)
        gone.add(later.id)
        lines.append(f"🔗 <code>#{later.id}</code> → <code>#{target.id}</code> {html.escape(target.title)} — {why}, birlashtirildi")
    await session.flush()
    return lines


async def run_data_fixes(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    try:
        async with session_factory() as session:
            if await session.get(AppMeta, FIX_KEY) is not None:
                return
            lines = await fix_cars(session)
            session.add(AppMeta(key=FIX_KEY, value=datetime.now(UTC).isoformat()))
            await session.commit()
    except Exception:
        logger.exception("Ma'lumot tuzatish xatosi")
        return
    logger.info("Ma'lumot tuzatildi: %d ta o'zgarish", len(lines))
    if lines:
        await notify_admins_text(
            bot,
            "🛠 <b>Bazadagi xatolar tuzatildi</b> (2 kunlik ish tahlili):\n" + "\n".join(lines[:40])
            + "\n\nXato bo'lsa — CRM yoki /mashina orqali qaytaring.",
        )
