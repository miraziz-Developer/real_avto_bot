"""Sotuvdagi barcha mashinalarni vaqtincha arxivga olish — egasi sotuvdagilarni kanaldan forward qilib qaytaradi.

Bot konteyneri ichida:
    docker compose exec -T bot python - [--apply] [--undo] < scripts/onsale_reset.py

--apply bo'lmasa — faqat ro'yxat. Har bir mashinaga «onsale_reset» hodisasi (avvalgi holati bilan) yoziladi,
shuning uchun --undo --apply hammasini avvalgi holatiga qaytaradi (forward qilib qaytarilganlariga tegmaydi).
Forward qilingan e'lon (bazada bor) arxivdan «sotuvda» ga qaytadi (bot/services/channel_import.confirm_forwarded).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from bot.config import settings
from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.models import AppMeta, Car, CarEvent, CarStatus

APPLY = "--apply" in sys.argv
UNDO = "--undo" in sys.argv
OPEN = (CarStatus.ACTIVE, CarStatus.REVIEW, CarStatus.RESERVED)
EVENT = "onsale_reset"
ONSALE_META_KEY = "onsale_check_started_at"


async def notify(text: str) -> None:
    from aiogram import Bot
    from aiogram.enums import ParseMode

    bot = Bot(settings.bot_token)
    try:
        for aid in settings.admin_telegram_ids:
            await bot.send_message(aid, text, parse_mode=ParseMode.HTML)
    finally:
        await bot.session.close()


async def reset(cars: CarRepository) -> int:
    rows = (await cars.session.execute(select(Car).where(Car.status.in_(OPEN)).order_by(Car.id))).scalars().all()
    print(f"Sotuvda/tekshiruvda/bronda: {len(rows)} ta")
    for c in rows:
        print(f"  #{c.id} {c.title} — {c.status} ({c.source})")
    if not APPLY:
        return len(rows)
    for c in rows:
        await cars.add_event(c, EVENT, {"old": c.status})
        await cars.set_status(c, CarStatus.ARCHIVED)
    now = datetime.now(UTC).isoformat()
    meta = await cars.session.get(AppMeta, ONSALE_META_KEY)
    if meta is None:
        cars.session.add(AppMeta(key=ONSALE_META_KEY, value=now))
    else:
        meta.value = now
    return len(rows)


async def undo(cars: CarRepository) -> int:
    events = (
        await cars.session.execute(select(CarEvent).where(CarEvent.kind == EVENT).order_by(CarEvent.id.desc()))
    ).scalars().all()
    restored, seen = 0, set()
    for ev in events:
        if ev.car_id in seen:
            continue
        seen.add(ev.car_id)
        car = await cars.get(ev.car_id)
        old = (ev.data or {}).get("old")
        if car is None or car.status != CarStatus.ARCHIVED or old not in OPEN:
            continue
        print(f"  #{car.id} {car.title} → {old}")
        if APPLY:
            await cars.set_status(car, old)
        restored += 1
    return restored


async def main() -> None:
    db_base.init_engine(settings.database_url, pool_size=2, max_overflow=0)
    async with db_base.get_session_factory()() as session:
        cars = CarRepository(session)
        n = await (undo(cars) if UNDO else reset(cars))
        if APPLY:
            await session.commit()
    await db_base.dispose_engine()
    if not APPLY:
        print("\nSinov rejimi — bazaga yozilmadi (--apply bilan yoziladi).")
        return
    if UNDO:
        print(f"\nQaytarildi: {n} ta")
        await notify(f"↩️ Vaqtincha arxivga olingan {n} ta mashina avvalgi holatiga qaytarildi.")
        return
    print(f"\nArxivga olindi: {n} ta")
    await notify(
        f"🗄 <b>Sotuvdagi {n} ta mashina vaqtincha sotuvdan olindi</b> (arxivga; o'chirilmadi).\n\n"
        "Endi kanaldan <b>hozir sotuvda turgan</b> har bir mashinaning e'lon postini shu botga forward qiling — "
        "har biri darhol «✅ sotuvda» bo'ladi (bazada yo'g'i yangi qo'shiladi).\n"
        "Faqat matnli e'lon postini yuboring (dumaloq video shart emas).\n\n"
        "Yangi e'lonlar, «SOTILDI» tahrirlari — avvalgidek bot o'zi kuzatadi."
    )


asyncio.run(main())
