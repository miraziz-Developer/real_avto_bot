"""Kanalning veb ko'rinishi orqali sinxronizatsiya: sahifani o'qish, o'tkazib yuborilgan e'lonlar, «SOTILDI», arxiv."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import Car, CarSource, CarStatus
from bot.services.car_parser import ParsedCar
from bot.services.channel_web import WebPost, listings, parse_page
from bot.workers import channel_sync

TEST_DB = os.getenv("TEST_DATABASE_URL")
CHAT = -1003558226604
USER = "REAL_AVTO_ARZON"
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)

LISTING = "🚘AVTOMOBIL: SPARK\n🗓️yili: 2018\n📍probeg: 85 000\n📞Aloqa: +998 90 123 45 67"


def _block(pid: int, when: datetime, body: str = "", *, round_video: bool = False) -> str:
    rv = '<div class="tgme_widget_message_roundvideo_wrap"></div>' if round_video else ""
    txt = f'<div class="tgme_widget_message_text js-message_text" dir="auto">{body}</div>' if body else ""
    return (
        f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" '
        f'data-post="{USER}/{pid}">{rv}{txt}<time datetime="{when.isoformat()}" class="time"></time></div></div>'
    )


def test_parse_page_and_listings():
    t = NOW - timedelta(hours=1)
    page = (
        _block(10, t, round_video=True)
        + _block(11, t + timedelta(minutes=1), round_video=True)
        + _block(12, t + timedelta(minutes=2), LISTING.replace("\n", "<br/>").replace("'", "&#39;"))
        + _block(13, t + timedelta(minutes=30), "BUGUN NASIYA SOVDASI BOR")
    )
    posts = parse_page(page, USER)
    assert [p.id for p in posts] == [10, 11, 12, 13]
    assert posts[0].round_video and not posts[2].round_video
    assert "yili: 2018" in posts[2].text
    items = listings(posts)
    assert len(items) == 1
    assert items[0].post.id == 12 and sorted(items[0].message_ids) == [10, 11, 12]


class Capture:
    def __init__(self) -> None:
        self.cards: list[tuple[int, str]] = []
        self.texts: list[str] = []

    async def card(self, bot, car, *, header=None, **kw):
        self.cards.append((car.id, header or ""))

    async def text(self, bot, text_):
        self.texts.append(text_)

    async def wishlist(self, *a, **kw):
        return None


@pytest.fixture
async def factory(monkeypatch):
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL berilmagan")
    await db_base.dispose_engine()
    f = db_base.init_engine(TEST_DB, pool_size=2, max_overflow=0)
    await db_base.create_tables()
    await apply_car_indexes(db_base.get_engine())
    async with db_base.get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE car_events, cars RESTART IDENTITY CASCADE"))
    cap = Capture()
    monkeypatch.setattr(channel_sync, "send_car_card_to_admins", cap.card)
    monkeypatch.setattr(channel_sync, "notify_admins_text", cap.text)
    monkeypatch.setattr(channel_sync, "notify_wishlist_matches_car", cap.wishlist)
    yield f, cap
    await db_base.dispose_engine()


async def _car(f, *, ids, status=CarStatus.ACTIVE, published=NOW - timedelta(days=2), texts=None, **kw) -> int:
    base = dict(brand="Chevrolet", model="Spark", year=2018, mileage_km=85000)
    base.update(kw)
    async with f() as s:
        car = await CarRepository(s).create_from_parsed(
            ParsedCar(**base),
            source=CarSource.CHANNEL,
            raw_text="x",
            channel_chat_id=CHAT,
            channel_message_ids=ids,
            status=status,
            published_at=published,
        )
        car.post_texts = texts or {}
        await s.commit()
        return car.id


async def _get(f, car_id: int) -> Car:
    async with f() as s:
        return (await s.execute(select(Car).where(Car.id == car_id))).scalar_one()


async def _all(f) -> list[Car]:
    async with f() as s:
        return list((await s.execute(select(Car).order_by(Car.id))).scalars().all())


async def test_missed_listing_is_created_and_fresh_one_waits(factory):
    f, cap = factory
    posts = [
        WebPost(20, NOW - timedelta(hours=2, minutes=1), "", round_video=True),
        WebPost(21, NOW - timedelta(hours=2), LISTING),
        # Hali jonli ishlov berilayotgan bo'lishi mumkin — tegmaymiz
        WebPost(22, NOW - timedelta(minutes=2), LISTING.replace("SPARK", "COBALT").replace("2018", "2020")),
        # Eski tarix — import ishi
        WebPost(23, NOW - timedelta(days=5), LISTING.replace("SPARK", "NEXIA 3").replace("2018", "2019")),
    ]
    res = await channel_sync.apply_listings(None, f, listings(posts), chat_id=CHAT, username=USER, now=NOW)
    cars = await _all(f)
    assert len(cars) == 1 and res.created == [cars[0].id]
    car = cars[0]
    assert car.model == "Spark" and car.year == 2018 and car.status == CarStatus.ACTIVE
    assert sorted(car.channel_message_ids) == [20, 21]
    assert "qo'shildi" in cap.cards[0][1]
    # Ikkinchi marta — dublikat yo'q
    res = await channel_sync.apply_listings(None, f, listings(posts), chat_id=CHAT, username=USER, now=NOW)
    assert not res.created and len(await _all(f)) == 1


async def test_sold_edit_and_phone_removal(factory):
    f, cap = factory
    a = await _car(f, ids=[30], texts={"30": LISTING})
    b = await _car(f, ids=[31], model="Cobalt", year=2020, texts={"31": LISTING})
    c = await _car(f, ids=[32], model="Gentra", year=2019)  # avvalgi matni noma'lum
    posts = [
        WebPost(30, NOW - timedelta(days=1), "❗️—SOTILDI—❗️\n" + LISTING),
        WebPost(31, NOW - timedelta(days=1), LISTING.replace("\n📞Aloqa: +998 90 123 45 67", "").replace("SPARK", "COBALT").replace("2018", "2020")),
        WebPost(32, NOW - timedelta(days=1), LISTING.replace("\n📞Aloqa: +998 90 123 45 67", "").replace("SPARK", "GENTRA").replace("2018", "2019")),
    ]
    res = await channel_sync.apply_listings(None, f, listings(posts), chat_id=CHAT, username=USER, now=NOW)
    assert sorted(res.sold) == [a, b]
    assert (await _get(f, a)).status == CarStatus.SOLD
    assert (await _get(f, b)).status == CarStatus.SOLD
    # Telefon avval bor edimi — bilmaymiz: sotildi deb hisoblamaymiz
    assert (await _get(f, c)).status == CarStatus.ACTIVE


async def test_repost_marks_seen_and_reactivates_archived(factory):
    f, cap = factory
    active = await _car(f, ids=[40], texts={"40": LISTING})
    archived = await _car(f, ids=[41], status=CarStatus.ARCHIVED, model="Matiz", year=2014, mileage_km=120000)
    posts = [
        WebPost(40, NOW - timedelta(hours=3), LISTING),
        WebPost(50, NOW - timedelta(hours=1), LISTING.replace("SPARK", "MATIZ").replace("2018", "2014").replace("85 000", "120 000")),
    ]
    res = await channel_sync.apply_listings(None, f, listings(posts), chat_id=CHAT, username=USER, now=NOW)
    assert res.reactivated == [archived] and not res.created
    car = await _get(f, archived)
    assert car.status == CarStatus.ACTIVE and 50 in car.channel_message_ids
    assert (await _get(f, active)).stale_prompted_at == NOW - timedelta(hours=3)


async def test_archive_stale_and_mass_guard(factory):
    f, cap = factory
    cutoff = NOW - timedelta(days=21)
    old = await _car(f, ids=[5], published=NOW - timedelta(days=30))
    seen = await _car(f, ids=[6, 120], published=NOW - timedelta(days=30), model="Cobalt", year=2020)
    fresh = await _car(f, ids=[7], published=NOW - timedelta(days=3), model="Gentra", year=2019)
    in_window = await _car(f, ids=[115], published=NOW - timedelta(days=30), model="Nexia 3", year=2019)
    posts = [
        WebPost(110, NOW - timedelta(days=20), "BUGUN NASIYA"),
        WebPost(115, NOW - timedelta(days=15), "kelorasila"),
        WebPost(120, NOW - timedelta(days=2), LISTING.replace("SPARK", "COBALT").replace("2018", "2020")),
    ]
    archived = await channel_sync.archive_stale(None, f, posts, chat_id=CHAT, cutoff=cutoff, days=21)
    assert archived == [old]
    assert (await _get(f, old)).status == CarStatus.ARCHIVED
    for cid in (seen, fresh, in_window):
        assert (await _get(f, cid)).status == CarStatus.ACTIVE
    assert "arxivga" in cap.texts[-1]

    # Ko'pchilik birdan «eskirgan» — hech narsa arxivlanmaydi
    for i in range(7):
        await _car(f, ids=[i + 60], published=NOW - timedelta(days=40), model="Damas", year=2010 + i)
    assert await channel_sync.archive_stale(None, f, posts, chat_id=CHAT, cutoff=cutoff, days=21) == []
    assert "xavfsizlik" in cap.texts[-1]


async def test_daily_report(factory):
    from bot.workers.daily_report import build_report

    f, _ = factory
    await _car(f, ids=[1])
    sold = await _car(f, ids=[2], model="Cobalt", year=2020)
    async with f() as s:
        car = await CarRepository(s).get(sold)
        await CarRepository(s).set_status(car, CarStatus.SOLD)
        await s.commit()
    async with f() as s:
        report = await build_report(s, datetime.now(UTC))
    assert "Kunlik hisobot" in report and "Sotuvda: <b>1</b>" in report
    assert f"#{sold}" in report


async def test_review_walks_open_cars_in_order(factory):
    from bot.handlers.car_review import _next_car, review_kb, review_text

    f, _ = factory
    a = await _car(f, ids=[1])
    await _car(f, ids=[2], status=CarStatus.SOLD, model="Cobalt", year=2020)
    c = await _car(f, ids=[3], status=CarStatus.REVIEW, model="Gentra", year=2019)
    async with f() as s:
        cars = CarRepository(s)
        car, left = await _next_car(cars, 0)
        assert car.id == a and left == 2
        car, left = await _next_car(cars, a)
        assert car.id == c and left == 1
        assert await _next_car(cars, c) == (None, 0)
        text_ = review_text(car, 0)
        assert "Aniqlash" in text_ and "sotildimi" in text_
        assert {b.callback_data for row in review_kb(car).inline_keyboard for b in row} >= {f"rv:sold:{c}", f"rv:keep:{c}"}


async def test_forward_confirms_on_sale_and_lists_the_rest(factory):
    from bot.handlers.car_review import onsale_split
    from bot.services.channel_import import confirm_forwarded

    f, _ = factory
    start = datetime.now(UTC) - timedelta(seconds=1)
    on_sale = await _car(f, ids=[1])
    was_sold = await _car(f, ids=[2], status=CarStatus.SOLD, model="Cobalt", year=2020)
    not_forwarded = await _car(f, ids=[3], model="Gentra", year=2019)
    really_sold = await _car(f, ids=[4], model="Damas", year=2020)
    async with f() as s:
        cars = CarRepository(s)
        r1 = await confirm_forwarded(cars, await cars.get(on_sale), LISTING)
        r2 = await confirm_forwarded(cars, await cars.get(was_sold), LISTING)
        r3 = await confirm_forwarded(cars, await cars.get(really_sold), "❗️—SOTILDI—❗️\n" + LISTING)
        # Matnsiz (dumaloq video) forward sotilganni qayta sotuvga chiqarmaydi
        r4 = await confirm_forwarded(cars, await cars.get(really_sold), "")
        await s.commit()
    assert r1.startswith("✅") and "qaytarildi" in r2 and r3.startswith("🔴") and r4.startswith("ℹ️")
    assert (await _get(f, was_sold)).status == CarStatus.ACTIVE
    assert (await _get(f, really_sold)).status == CarStatus.SOLD
    async with f() as s:
        confirmed, rest = await onsale_split(CarRepository(s), start)
    assert [c.id for c in confirmed] == [on_sale, was_sold]
    assert [c.id for c in rest] == [not_forwarded]
