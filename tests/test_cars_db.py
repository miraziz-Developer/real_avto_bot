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
        # Yil yo'q — tekshiruv kerak (narx esa majburiy emas)
        car = await cars.create_from_parsed(_parsed(year=None), source=CarSource.CHANNEL, raw_text="x")
        assert car.status == CarStatus.REVIEW
        changes = await cars.apply_parsed(car, _parsed(year=2021))
        await s.commit()
        assert "year" in changes
        assert car.status == CarStatus.ACTIVE
        # Narxsiz mashina ham sotuvda bo'ladi (Real Avto postlarida narx ko'pincha yozilmaydi)
        no_price = await cars.create_from_parsed(_parsed(price_usd=None), source=CarSource.CHANNEL, raw_text="x")
        assert no_price.status == CarStatus.ACTIVE


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
    channel_watch._orphan_media.clear()  # testlar orasida «egasiz media» buferi aralashmasin
    channel_watch._recent_media_car.clear()
    channel_watch._transcript_cache.clear()
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


async def test_video_note_then_reply_with_details_becomes_one_car(session_factory, watch):
    from aiogram.types import VideoNote

    bot = FakeBot()
    video_post = Message(
        message_id=80,
        date=datetime.now(timezone.utc),
        chat=Chat(id=CHANNEL_ID, type="channel", username="real_avto_test"),
        video_note=VideoNote(file_id="vnote1", file_unique_id="u80", length=240, duration=30),
    )
    # Matnsiz, ovozsiz dumaloq video — o'zi e'lon emas
    await watch.process_channel_post(bot, [video_post])
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalars().all() == []

    # Jamoa unga reply qilib ma'lumot yozadi
    await watch.process_channel_post(
        bot, [_channel_msg(81, text_="Cobalt 2020, probeg 98 000 km, narxi 9200$", reply_to=video_post)]
    )
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert (car.model, car.year, car.price_usd) == ("Cobalt", 2020, 9200)
        assert car.channel_message_ids == [80, 81]
        assert car.video_file_ids == ["vn:vnote1"]

    # Keyinroq yana reply — narx tushdi: o'sha mashina yangilanadi, yangi yozuv yaratilmaydi
    await watch.process_channel_post(bot, [_channel_msg(82, text_="Narx tushdi: 8800$", reply_to=video_post)])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert car.price_usd == 8800 and car.channel_message_ids == [80, 81, 82]
    assert any("reply bilan yangilandi" in t for _, _, t in bot.sent)


async def test_edit_removing_phone_or_baraka_reply_marks_sold_and_bron_reply(session_factory, watch):
    bot = FakeBot()
    text = "Damas 2022, probeg 76 000 km, narxi 7800$\n📞 +998 97 782 92 99"
    await watch.process_channel_post(bot, [_channel_msg(90, text_=text)])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert car.status == CarStatus.ACTIVE
        # 2 kundan keyin: postdan telefon olib tashlandi
        await watch.on_channel_post_edited(_channel_msg(90, text_="Damas 2022, probeg 76 000 km, narxi 7800$"), bot, CarRepository(s))
        await s.refresh(car)
        assert car.status == CarStatus.SOLD
    assert any("telefon raqami olib tashlandi" in t for _, _, t in bot.sent)

    # Boshqa mashina: «bron» reply → bron, keyin «Baraka bo'ldi» reply → sotildi
    post = _channel_msg(91, text_="Cobalt 2020, probeg 98 000 km, narxi 9200$")
    await watch.process_channel_post(bot, [post])
    await watch.process_channel_post(bot, [_channel_msg(92, text_="Bron", reply_to=post)])
    async with session_factory() as s:
        cobalt = (await s.execute(select(Car).where(Car.model == "Cobalt"))).scalar_one()
        assert cobalt.status == CarStatus.RESERVED
    await watch.process_channel_post(bot, [_channel_msg(93, text_="Baraka bo'ldi ✅", reply_to=post)])
    async with session_factory() as s:
        cobalt = (await s.execute(select(Car).where(Car.model == "Cobalt"))).scalar_one()
        assert cobalt.status == CarStatus.SOLD


async def test_new_post_with_baraka_wording_is_not_sold(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(95, text_="Spark 2021, 31000 km, narxi 8500$. Barakasini bersin!")])
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalar_one().status == CarStatus.ACTIVE


async def test_forwarded_video_then_description_becomes_one_car_with_original_date(session_factory, watch):
    from aiogram.types import MessageOriginChannel, VideoNote

    bot = FakeBot()
    original_date = datetime.now(timezone.utc) - timedelta(days=5)
    origin = MessageOriginChannel(
        date=original_date, chat=Chat(id=-100777, type="channel", username="real_avto_arzon"), message_id=500
    )
    video = Message(
        message_id=200,
        date=datetime.now(timezone.utc),
        chat=Chat(id=CHANNEL_ID, type="channel", username="real_avto_test"),
        video_note=VideoNote(file_id="fwd_vn", file_unique_id="u200", length=240, duration=20),
        forward_origin=origin,
    )
    await watch.process_channel_post(bot, [video])
    description = Message(
        message_id=201,
        date=datetime.now(timezone.utc),
        chat=Chat(id=CHANNEL_ID, type="channel", username="real_avto_test"),
        text="Gentra 2019, probeg 120 ming km, narxi 9800$",
        forward_origin=MessageOriginChannel(date=original_date, chat=origin.chat, message_id=501),
    )
    await watch.process_channel_post(bot, [description])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert (car.model, car.price_usd) == ("Gentra", 9800)
        assert car.channel_message_ids == [200, 201] and car.video_file_ids == ["vn:fwd_vn"]
        assert abs((car.published_at - original_date).total_seconds()) < 2  # asl post sanasi


async def test_editing_other_text_does_not_trigger_phone_rule(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(96, text_="Malibu 2019, probeg 64 000 km, narxi 21500$\n📞 +998 97 782 92 99")])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        # Butunlay boshqa matnga tahrir (telefonsiz) — bu sotildi emas
        await watch.on_channel_post_edited(_channel_msg(96, text_="Yangi kelgan mashinalar ro'yxati tez orada"), bot, CarRepository(s))
        await s.refresh(car)
        assert car.status == CarStatus.ACTIVE



async def test_spoken_video_and_description_processed_concurrently_make_one_car(session_factory, watch, monkeypatch):
    import asyncio

    from aiogram.types import VideoNote

    async def slow_transcribe(bot, messages):
        if any(m.video_note for m in messages):
            await asyncio.sleep(0.3)  # haqiqiy transkripsiya sekin
            return ["Cobalt 2020 yil, mexanika"]
        return []

    monkeypatch.setattr(watch, "_transcribe_media", slow_transcribe)
    bot = FakeBot()
    chat = Chat(id=CHANNEL_ID, type="channel", username="real_avto_test")
    video = Message(
        message_id=300, date=datetime.now(timezone.utc), chat=chat,
        video_note=VideoNote(file_id="spoken", file_unique_id="u300", length=240, duration=20),
    )
    description = Message(
        message_id=301, date=datetime.now(timezone.utc), chat=chat, text="Cobalt narxi 9200$, probeg 98 000 km"
    )
    # Telegram ikkalasini ketma-ket yuboradi, aiogram esa parallel qayta ishlaydi
    await asyncio.gather(watch.process_channel_post(bot, [video]), watch.process_channel_post(bot, [description]))
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1
        car = rows[0]
        assert (car.model, car.year, car.price_usd, car.mileage_km) == ("Cobalt", 2020, 9200, 98000)
        assert car.channel_message_ids == [300, 301] and car.video_file_ids == ["vn:spoken"]


async def test_audio_only_facts_wait_for_admin_but_text_posts_go_live(session_factory, watch, monkeypatch):
    from aiogram.types import VideoNote

    async def fake_transcribe(bot, messages):
        return ["Kobalt 2020 yil, narxi 9200 dollar"] if any(m.video_note for m in messages) else []

    monkeypatch.setattr(watch, "_transcribe_media", fake_transcribe)
    bot = FakeBot()
    chat = Chat(id=CHANNEL_ID, type="channel", username="real_avto_test")
    video = Message(
        message_id=400, date=datetime.now(timezone.utc), chat=chat,
        video_note=VideoNote(file_id="v400", file_unique_id="u400", length=240, duration=20),
    )
    await watch.process_channel_post(bot, [video])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert car.status == CarStatus.REVIEW  # faqat ovozdan — admin tasdig'isiz sotuvga chiqmaydi
    card = bot.sent[-1][2]
    assert "ovozdan olindi" in card and "Kobalt 2020" in card

    # Matnli post (+ ovoz) — faktlar matnda bor, darhol sotuvda
    watch._recent_media_car.clear()
    text_post = Message(
        message_id=401, date=datetime.now(timezone.utc), chat=chat,
        caption="🚘Avtomobil: Gentra 🗓️yili: 2019 📍probeg: 120.000km",
        video_note=None,
    )
    await watch.process_channel_post(bot, [text_post])
    async with session_factory() as s:
        gentra = (await s.execute(select(Car).where(Car.model == "Gentra"))).scalar_one()
        assert gentra.status == CarStatus.ACTIVE



def _vnote(mid: int, uid: str) -> Message:
    from aiogram.types import VideoNote

    return Message(
        message_id=mid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=CHANNEL_ID, type="channel", username="real_avto_test"),
        video_note=VideoNote(file_id=uid, file_unique_id=f"u{mid}", length=240, duration=55),
    )


async def test_two_round_videos_for_one_car_and_next_car_separately(session_factory, watch, monkeypatch):
    speech = {
        500: "Kobalt 2020 yil, mexanika",
        501: "yurgani 98 ming, kraskasi toza",  # o'sha Cobalt'ning davomi
        502: "Spark 2015 yil sotiladi",  # boshqa mashina
    }

    async def fake_transcribe(bot, messages):
        return [speech[m.message_id] for m in messages if m.message_id in speech]

    monkeypatch.setattr(watch, "_transcribe_media", fake_transcribe)
    bot = FakeBot()
    for mid in (500, 501, 502):
        await watch.process_channel_post(bot, [_vnote(mid, f"v{mid}")])
    async with session_factory() as s:
        rows = (await s.execute(select(Car).order_by(Car.id))).scalars().all()
        assert [r.model for r in rows] == ["Cobalt", "Spark"]
        cobalt = rows[0]
        assert cobalt.channel_message_ids == [500, 501] and cobalt.video_file_ids == ["vn:v500", "vn:v501"]
        assert cobalt.mileage_km == 98000
        assert cobalt.status == CarStatus.REVIEW  # faqat ovozdan — 2-video ham o'zicha sotuvga chiqarmaydi
    assert any("yana video qo'shildi" in t for _, _, t in bot.sent)


async def test_two_silent_videos_then_description_make_one_car(session_factory, watch, monkeypatch):
    async def silent(bot, messages):
        return []

    monkeypatch.setattr(watch, "_transcribe_media", silent)
    bot = FakeBot()
    await watch.process_channel_post(bot, [_vnote(600, "s1")])
    await watch.process_channel_post(bot, [_vnote(601, "s2")])
    await watch.process_channel_post(bot, [_channel_msg(602, text_="Nexia 3, yili 2018, probeg 120 000 km")])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert car.model == "Nexia 3" and car.channel_message_ids == [600, 601, 602]
        assert car.video_file_ids == ["vn:s1", "vn:s2"] and car.status == CarStatus.ACTIVE


async def test_reposted_post_merges_into_existing_car(session_factory, watch):
    """Admin mashinani narx tushirib qayta tashladi — ikkinchi mashina yaratilmaydi, narx yangilanadi."""
    bot = FakeBot()
    await watch.process_channel_post(
        bot, [_channel_msg(700, text_="Cobalt 2020, probeg 98 000 km, narxi 9500$")]
    )
    await watch.process_channel_post(
        bot, [_channel_msg(710, text_="🔥 Narx tushdi! Cobalt 2020, probeg 98 000 km, narxi 9200$")]
    )
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1
        car = rows[0]
        assert car.price_usd == 9200 and car.channel_message_ids == [700, 710]
    assert "qayta joylandi" in bot.sent[-1][2]

    # Yangi postga «sotildi» reply ham shu mashinani topadi
    await watch.process_channel_post(bot, [_channel_msg(711, text_="sotildi", reply_to=_channel_msg(710, text_="x"))])
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalar_one().status == CarStatus.SOLD


async def test_same_model_different_car_is_not_merged(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(720, text_="Cobalt 2020, probeg 98 000 km, narxi 9500$")])
    await watch.process_channel_post(bot, [_channel_msg(721, text_="Cobalt 2020, probeg 45 000 km, narxi 10500$")])
    await watch.process_channel_post(bot, [_channel_msg(722, text_="Cobalt 2021, probeg 98 000 km, narxi 11000$")])
    async with session_factory() as s:
        assert len((await s.execute(select(Car))).scalars().all()) == 3


async def test_sold_car_reposted_creates_new_record(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(730, text_="Nexia 3 2018, probeg 120 000 km, narxi 7500$")])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        await CarRepository(s).set_status(car, CarStatus.SOLD)
        await s.commit()
    await watch.process_channel_post(bot, [_channel_msg(731, text_="Nexia 3 2018, probeg 120 000 km, narxi 7500$")])
    async with session_factory() as s:
        statuses = sorted(c.status for c in (await s.execute(select(Car))).scalars().all())
        assert statuses == sorted([CarStatus.SOLD, CarStatus.ACTIVE])


# --- Kanal kuzatuvi: sotildi/bron aniqligi va o'chirilgan postlar ------------------------------
async def test_separate_sold_announcement_marks_existing_car(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(800, text_="Cobalt 2020, probeg 98 000 km, narxi 9500$")])
    # Yangi alohida post (reply emas): «sotildi»
    await watch.process_channel_post(bot, [_channel_msg(801, text_="✅ Cobalt 2020, probeg 98 000 km — SOTILDI")])
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1 and rows[0].status == CarStatus.SOLD and rows[0].channel_message_ids == [800, 801]


async def test_question_or_negated_sold_reply_does_not_mark_sold(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(810, text_="Nexia 3 2019, probeg 60 000 km, narxi 8200$")])
    for i, t in enumerate(("sotildimi?", "sotildi?", "hali sotilgan emas")):
        await watch.process_channel_post(bot, [_channel_msg(811 + i, text_=t, reply_to=_channel_msg(810, text_="x"))])
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalar_one().status == CarStatus.ACTIVE


async def test_edit_adding_and_removing_bron(session_factory, watch):
    bot = FakeBot()
    base = "Gentra 2021, probeg 40 000 km, narxi 12500$"
    await watch.process_channel_post(bot, [_channel_msg(820, text_=base)])
    async with session_factory() as s:
        await watch.on_channel_post_edited(_channel_msg(820, text_=base + "\nBRON ✅"), bot, CarRepository(s))
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalar_one().status == CarStatus.RESERVED
    async with session_factory() as s:
        await watch.on_channel_post_edited(_channel_msg(820, text_=base), bot, CarRepository(s))
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalar_one().status == CarStatus.ACTIVE
    # «Bron qilish mumkin» — taklif, bron emas
    async with session_factory() as s:
        await watch.on_channel_post_edited(
            _channel_msg(820, text_=base + "\nBron qilish mumkin"), bot, CarRepository(s)
        )
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalar_one().status == CarStatus.ACTIVE


class _ProbeBot(FakeBot):
    """editMessageReplyMarkup: ro'yxatdagi xabarlar o'chirilgan, qolganlari — boshqa admin posti."""

    def __init__(self, deleted: set[int]) -> None:
        super().__init__()
        self.deleted = deleted
        self.probed: list[int] = []

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **kw):
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import EditMessageReplyMarkup

        self.probed.append(message_id)
        m = EditMessageReplyMarkup(chat_id=chat_id, message_id=message_id)
        if message_id in self.deleted:
            raise TelegramBadRequest(method=m, message="Bad Request: message to edit not found")
        raise TelegramBadRequest(method=m, message="Bad Request: message can't be edited")


async def test_deleted_channel_post_takes_car_off_sale(session_factory, watch, monkeypatch):
    from bot.workers import post_watch

    monkeypatch.setattr(post_watch, "PROBE_PAUSE_SECONDS", 0)
    monkeypatch.setattr(post_watch, "settings", watch.settings)
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(830, text_="Spark 2018, probeg 70 000 km, narxi 6500$")])
    await watch.process_channel_post(bot, [_channel_msg(831, text_="Malibu 2019, probeg 90 000 km, narxi 19500$")])
    await watch.process_channel_post(bot, [_channel_msg(832, text_="Tracker 2022, probeg 20 000 km, narxi 21000$")])

    probe = _ProbeBot(deleted={830})
    assert await post_watch.check_deleted_posts_once(probe, session_factory) == 1
    async with session_factory() as s:
        by_model = {c.model: c.status for c in (await s.execute(select(Car))).scalars().all()}
    assert by_model == {"Spark": CarStatus.ARCHIVED, "Malibu": CarStatus.ACTIVE, "Tracker": CarStatus.ACTIVE}
    assert any("post o'chirilgan" in t for _, _, t in probe.sent)

    # Agent arxivdagi mashinani taklif qilmaydi
    async with session_factory() as s:
        offer = await CarRepository(s).search_offerable(model="Spark")
        assert offer == []


async def test_mass_deletion_guard_changes_nothing(session_factory, watch, monkeypatch):
    from bot.workers import post_watch

    monkeypatch.setattr(post_watch, "PROBE_PAUSE_SECONDS", 0)
    monkeypatch.setattr(post_watch, "settings", watch.settings)
    monkeypatch.setattr(post_watch, "notify_admins_text", _noop_notify)
    bot = FakeBot()
    models = ["Spark", "Nexia 3", "Cobalt", "Gentra", "Malibu", "Tracker"]
    for i, m in enumerate(models):
        await watch.process_channel_post(bot, [_channel_msg(840 + i, text_=f"{m} 2020, probeg {50 + i} 000 km, narxi {8000 + i}$")])
    probe = _ProbeBot(deleted=set(range(840, 846)))  # hammasi «o'chirilgan» — shubhali
    assert await post_watch.check_deleted_posts_once(probe, session_factory) == 0
    async with session_factory() as s:
        assert {c.status for c in (await s.execute(select(Car))).scalars().all()} == {CarStatus.ACTIVE}


async def _noop_notify(bot, text):
    return None


async def test_posts_with_buttons_are_not_probed(session_factory, watch, monkeypatch):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot.workers import post_watch

    monkeypatch.setattr(post_watch, "PROBE_PAUSE_SECONDS", 0)
    bot = FakeBot()
    msg = _channel_msg(850, text_="Damas 2021, probeg 30 000 km, narxi 8800$")
    msg = msg.model_copy(update={"reply_markup": InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="👍", callback_data="like")]]
    )})
    await watch.process_channel_post(bot, [msg])
    probe = _ProbeBot(deleted={850})
    await post_watch.check_deleted_posts_once(probe, session_factory)
    assert probe.probed == []  # tugmasi o'chib ketmasligi uchun tekshirilmaydi


# --- Eski (bazada yo'q) postlar va sotuvdan keyingi videolar -----------------------------------


async def test_sold_reply_to_old_unknown_post_restores_car_as_sold(session_factory, watch):
    bot = FakeBot()
    old_post = _channel_msg(900, text_="Malibu 2 2018, probeg 110 000 km, narxi 17500$")  # bot ulanmasdan oldin
    await watch.process_channel_post(bot, [_channel_msg(901, text_="Sotildi ✅", reply_to=old_post)])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert (car.model, car.year, car.status) == ("Malibu", 2018, CarStatus.SOLD)
        assert car.channel_message_ids == [900]
    assert "Eski post" in bot.sent[-1][2]


async def test_info_reply_to_old_unknown_post_creates_car(session_factory, watch):
    bot = FakeBot()
    old_post = _channel_msg(905, text_="Spark 2016, probeg 90 000 km")
    await watch.process_channel_post(bot, [_channel_msg(906, text_="narxi 6800$", reply_to=old_post)])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert (car.model, car.price_usd, car.status) == ("Spark", 6800, CarStatus.ACTIVE)


async def test_sold_reply_to_unrecognizable_post_only_alerts(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(908, text_="sotildi", reply_to=_channel_msg(907, text_="🔥🔥"))])
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalars().all() == []
    assert "aniqlab bo'lmadi" in bot.sent[-1][2]


async def test_round_video_reply_saying_sold_marks_car_sold(session_factory, watch, monkeypatch):
    async def fake_transcribe(bot, messages):
        return ["mashina sotildi, xaridorga barakasini bersin"] if any(m.video_note for m in messages) else []

    monkeypatch.setattr(watch, "_transcribe_media", fake_transcribe)
    bot = FakeBot()
    post = _channel_msg(910, text_="Cobalt 2020, probeg 98 000 km, narxi 9500$")
    await watch.process_channel_post(bot, [post])
    video = _vnote(911, "thanks")
    video = video.model_copy(update={"reply_to_message": post})
    await watch.process_channel_post(bot, [video])
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1 and rows[0].status == CarStatus.SOLD
    assert "Eshitilgani" in bot.sent[-1][2]


async def test_standalone_thanks_video_marks_matching_car_without_new_record(session_factory, watch, monkeypatch):
    speech = {921: "Real Avtodan Kobalt sotib oldim, rahmat", 922: "Yangi Spark muborak bo'lsin"}

    async def fake_transcribe(bot, messages):
        return [speech[m.message_id] for m in messages if m.message_id in speech]

    monkeypatch.setattr(watch, "_transcribe_media", fake_transcribe)
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(920, text_="Cobalt 2020, probeg 98 000 km, narxi 9500$")])
    await watch.process_channel_post(bot, [_vnote(921, "t1")])
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1 and rows[0].status == CarStatus.SOLD
    assert "Faqat model bo'yicha" in bot.sent[-1][2]

    # Bazada mos mashina yo'q — keraksiz «tekshiruv» yozuvi yaratilmaydi, admin ogohlantiriladi
    await watch.process_channel_post(bot, [_vnote(922, "t2")])
    async with session_factory() as s:
        assert len((await s.execute(select(Car))).scalars().all()) == 1
    assert "topa olmadim" in bot.sent[-1][2]


async def test_thanks_text_post_with_several_matches_asks_admins(session_factory, watch):
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(930, text_="Gentra 2019, probeg 80 000 km, narxi 11000$")])
    await watch.process_channel_post(bot, [_channel_msg(931, text_="Gentra 2021, probeg 30 000 km, narxi 13500$")])
    sent_before = len(bot.sent)
    await watch.process_channel_post(bot, [_channel_msg(932, text_="Mijozimizga Gentra muborak bo'lsin! 🎉")])
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 2 and all(r.status == CarStatus.ACTIVE for r in rows)
    texts = [t for _, _, t in bot.sent[sent_before:]]
    assert "2 ta mos mashina" in texts[0] and sum("Shu mashina sotildimi" in t for t in texts) == 2


async def test_edit_of_old_unknown_post(session_factory, watch):
    bot = FakeBot()
    async with session_factory() as s:
        await watch._on_channel_post_edited(
            _channel_msg(940, text_="Nexia 3 2019, probeg 60 000 km, narxi 8200$"), bot, CarRepository(s)
        )
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert car.status == CarStatus.ACTIVE and car.channel_message_ids == [940]

    # Boshqa eski post tahrirda «SOTILDI» bo'ldi — tarix uchun sotilgan yozuv
    async with session_factory() as s:
        await watch._on_channel_post_edited(
            _channel_msg(941, text_="Lacetti 2015, probeg 150 000 km, narxi 8000$ — SOTILDI"), bot, CarRepository(s)
        )
    async with session_factory() as s:
        lacetti = (await s.execute(select(Car).where(Car.model == "Lacetti"))).scalar_one()
        assert lacetti.status == CarStatus.SOLD


async def test_edit_of_old_unknown_post_with_bron_is_reserved(session_factory, watch):
    bot = FakeBot()
    async with session_factory() as s:
        await watch._on_channel_post_edited(
            _channel_msg(950, text_="Spark 2016, probeg 90 000 km, narxi 6800$ BRON"), bot, CarRepository(s)
        )
    async with session_factory() as s:
        assert (await s.execute(select(Car))).scalar_one().status == CarStatus.RESERVED
    assert "Eski post tahrirlandi" in bot.sent[-1][2]


async def test_sold_video_naming_lacetti_marks_gentra(session_factory, watch, monkeypatch):
    async def fake_transcribe(bot, messages):
        return ["Ha, sotildi, baraka bo'libdi. [Videoda ko'rinadi]: oq Chevrolet Lacetti"] if messages[0].video_note else []

    monkeypatch.setattr(watch, "_transcribe_media", fake_transcribe)
    bot = FakeBot()
    await watch.process_channel_post(bot, [_channel_msg(960, text_="Gentra 2018, probeg 120 000 km, narxi 11800$")])
    await watch.process_channel_post(bot, [_vnote(961, "lac")])
    async with session_factory() as s:
        rows = (await s.execute(select(Car))).scalars().all()
        assert len(rows) == 1 and rows[0].status == CarStatus.SOLD
    assert "Eshitilgani" in bot.sent[-1][2]


async def test_second_video_calling_gentra_lacetti_joins_same_car(session_factory, watch, monkeypatch):
    speech = {
        970: "Gentra 2019, avtomat, yurgani 297 ming, qora",
        971: "Mana oldi salonlari, metan, videoregistrator. [Videoda ko'rinadi]: qora Chevrolet Lacetti",
    }

    async def fake_transcribe(bot, messages):
        return [speech[m.message_id] for m in messages if m.message_id in speech]

    monkeypatch.setattr(watch, "_transcribe_media", fake_transcribe)
    bot = FakeBot()
    await watch.process_channel_post(bot, [_vnote(970, "g1")])
    await watch.process_channel_post(bot, [_vnote(971, "g2")])
    async with session_factory() as s:
        car = (await s.execute(select(Car))).scalar_one()
        assert car.model == "Gentra" and car.channel_message_ids == [970, 971]


def test_spoken_paint_and_absurd_mileage_are_ignored():
    from bot.services.car_parser import parse_car_text

    assert parse_car_text("kraska ikkilaydigan bo'lsak", usd_rate_uzs=12700).paint_status is None
    assert parse_car_text("kraska ikki joyda", usd_rate_uzs=12700).paint_status == "ikki joyda"
    assert parse_car_text("probeg 905 ming", usd_rate_uzs=12700).mileage_km is None
    assert parse_car_text("probeg 297 ming", usd_rate_uzs=12700).mileage_km == 297000
