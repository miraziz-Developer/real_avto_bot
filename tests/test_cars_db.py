"""Mashinalar bazasi va kanal kuzatuvchi — haqiqiy PostgreSQL bilan integratsion testlar.

Ishga tushirish: TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/realavto_test pytest tests/test_cars_db.py
O'zgaruvchi berilmasa testlar o'tkazib yuboriladi.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.types import Chat, Message, PhotoSize
from sqlalchemy import select, text

from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import Car, CarEvent, CarSource, CarStatus
from bot.services.car_parser import ParsedCar

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL berilmagan")

CHANNEL_ID = -1001234567890
ADMIN_ID = 111


@pytest.fixture
async def session_factory():
    await db_base.dispose_engine()
    factory = db_base.init_engine(TEST_DB, pool_size=2, max_overflow=0)
    await db_base.create_tables()
    await apply_car_indexes(db_base.get_engine())
    async with db_base.get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE cars, car_events RESTART IDENTITY CASCADE"))
    yield factory
    await db_base.dispose_engine()


def _parsed(**kw) -> ParsedCar:
    base = dict(brand="Chevrolet", model="Damas", year=2022, mileage_km=76000, price_usd=7800, confidence=0.9)
    base.update(kw)
    return ParsedCar(**base)


async def test_create_find_by_album_message_and_events(session_factory):
    async with session_factory() as s:
        cars = CarRepository(s)
        car = await cars.create_from_parsed(
            _parsed(), source=CarSource.CHANNEL, raw_text="x", channel_chat_id=CHANNEL_ID, channel_message_ids=[10, 11, 12]
        )
        await s.commit()
        assert car.status == CarStatus.ACTIVE
        # Albomning ikkinchi xabari bo'yicha ham topiladi (tahrir/reply qaysi xabarga kelsa ham)
        found = await cars.find_by_channel_message(CHANNEL_ID, 11)
        assert found is not None and found.id == car.id
        assert await cars.find_by_channel_message(CHANNEL_ID, 99) is None
        assert await cars.find_by_channel_message(-100999, 11) is None


async def test_incomplete_goes_to_review_then_active_after_fix(session_factory):
    async with session_factory() as s:
        cars = CarRepository(s)
        car = await cars.create_from_parsed(_parsed(price_usd=None), source=CarSource.CHANNEL, raw_text="x")
        assert car.status == CarStatus.REVIEW
        changes = await cars.apply_parsed(car, _parsed(price_usd=8100))
        await s.commit()
        assert "price_usd" in changes
        assert car.status == CarStatus.ACTIVE


async def test_price_change_and_status_events(session_factory):
    async with session_factory() as s:
        cars = CarRepository(s)
        car = await cars.create_from_parsed(_parsed(), source=CarSource.CHANNEL, raw_text="x")
        await cars.update_fields(car, {"price_usd": 7500}, actor=ADMIN_ID)
        await cars.set_status(car, CarStatus.SOLD, actor=ADMIN_ID, sold_price_usd=7400)
        await s.commit()
        kinds = [k for (k,) in (await s.execute(select(CarEvent.kind).where(CarEvent.car_id == car.id))).all()]
        assert kinds.count("price_changed") == 1
        assert "status_changed" in kinds
        assert car.sold_at is not None and car.sold_price_usd == 7400
        # Qayta sotuvga — sotilgan sana tozalanadi
        await cars.set_status(car, CarStatus.ACTIVE)
        assert car.sold_at is None


async def test_stats_and_profit(session_factory):
    now = datetime.now(timezone.utc)
    async with session_factory() as s:
        cars = CarRepository(s)
        a = await cars.create_from_parsed(_parsed(), source=CarSource.CHANNEL, raw_text="", published_at=now - timedelta(days=10))
        await cars.update_fields(a, {"purchase_price_usd": 7000, "expenses_usd": 200, "is_own": True})
        await cars.set_status(a, CarStatus.SOLD, sold_price_usd=7900)
        await cars.create_from_parsed(_parsed(model="Cobalt", price_usd=9000), source=CarSource.CHANNEL, raw_text="")
        await s.commit()
        st = await cars.stats(days=30)
        assert st["sold_count"] == 1
        assert st["by_status"][CarStatus.ACTIVE] == 1
        assert st["active_value_usd"] == 9000
        assert st["own_profit_usd"] == 700
        assert 9.5 <= st["avg_days_to_sell"] <= 10.5
        assert st["top_sold_models"] == [("Chevrolet Damas", 1)]


async def test_stale_cars_query(session_factory):
    now = datetime.now(timezone.utc)
    async with session_factory() as s:
        cars = CarRepository(s)
        old = await cars.create_from_parsed(_parsed(), source=CarSource.CHANNEL, raw_text="", published_at=now - timedelta(days=20))
        await cars.create_from_parsed(_parsed(), source=CarSource.CHANNEL, raw_text="", published_at=now - timedelta(days=2))
        await s.commit()
        rows = await cars.stale_active_cars(older_than_days=14)
        assert [c.id for c in rows] == [old.id]
        await cars.mark_stale_prompted(old)
        await s.commit()
        assert await cars.stale_active_cars(older_than_days=14) == []


# --- Kanal kuzatuvchi (process_channel_post) uchun soxta bot ---------------------------


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, int, str]] = []

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        self.sent.append(("photo", chat_id, caption or ""))

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("text", chat_id, text))


def _channel_msg(mid: int, *, caption: str | None = None, text_: str | None = None, photo: bool = False, reply_to=None):
    return Message(
        message_id=mid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=CHANNEL_ID, type="channel", username="real_avto_test"),
        caption=caption,
        text=text_,
        photo=[PhotoSize(file_id=f"ph{mid}", file_unique_id=f"u{mid}", width=10, height=10)] if photo else None,
        media_group_id="g1" if photo else None,
        reply_to_message=reply_to,
    )


@pytest.fixture
def watch(monkeypatch):
    from bot.handlers import channel_watch
    from bot.services import car_cards

    patched = dataclasses.replace(car_cards.settings, admin_telegram_ids=frozenset({ADMIN_ID}), channel_id=str(CHANNEL_ID))
    monkeypatch.setattr(car_cards, "settings", patched)
    monkeypatch.setattr(channel_watch, "settings", patched)
    return channel_watch


async def test_channel_album_becomes_one_car_and_reply_marks_sold(session_factory, watch):
    bot = FakeBot()
    album = [
        _channel_msg(50, caption="Damas 📆 yili: 2022\n📍 probeg: 76.000km\n💰 Narxi: 7 800$\nKraskasi toza", photo=True),
        _channel_msg(51, photo=True),
        _channel_msg(52, photo=True),
    ]
    await watch.process_channel_post(bot, album)
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1
        car = rows[0]
        assert (car.model, car.year, car.mileage_km, car.price_usd) == ("Damas", 2022, 76000, 7800)
        assert car.photo_file_ids == ["ph50", "ph51", "ph52"]
        assert car.channel_message_ids == [50, 51, 52]
        assert car.status == CarStatus.ACTIVE
    assert bot.sent and bot.sent[0][0] == "photo" and bot.sent[0][1] == ADMIN_ID

    # Takroriy update — ikkinchi yozuv yaratilmaydi
    await watch.process_channel_post(bot, album)
    # Albomning 2-rasmiga «SOTILDI» deb reply
    await watch.process_channel_post(bot, [_channel_msg(60, text_="SOTILDI ✅", reply_to=album[1])])
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1 and rows[0].status == CarStatus.SOLD


async def test_non_car_post_is_ignored(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(70, text_="Hammaga hayrli tong! Bugun ish vaqti 9:00 dan")])
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalars().all() == []
    assert bot.sent == []
