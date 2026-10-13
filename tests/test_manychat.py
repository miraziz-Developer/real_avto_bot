"""ManyChat → bot: Instagram komment qaysi mashina haqida, javob formati, admin bog'lashi, vaqt chegarasi."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select, text

from bot import config
from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import CarSource, CarStatus, InstagramPost, Lead
from bot.instagram import manychat
from bot.instagram.car_match import explicit_car_id, match_caption, post_ref, remember_post
from bot.instagram.manychat import MANYCHAT_PATH, ManyChatBridge, _field, lead_key
from bot.services.car_parser import ParsedCar

TEST_DB = os.getenv("TEST_DATABASE_URL")
ADMIN_ID = 111
SECRET = "mc-secret"
AUTH = {"Authorization": f"Bearer {SECRET}"}


def test_post_ref_and_explicit():
    assert post_ref("", "https://www.instagram.com/reel/AbCdE123/?igsh=x") == "AbCdE123"
    assert post_ref(None, "https://instagram.com/realavto/p/Xy_z-90/") == "Xy_z-90"
    assert post_ref("17890000", "https://instagram.com/p/Other1/") == "17890000"
    assert post_ref("", "") is None
    assert explicit_car_id("#17") == 17 and explicit_car_id(" 4 ") == 4
    assert explicit_car_id("Cobalt") is None and explicit_car_id(None) is None
    # ManyChat bo'sh o'zgaruvchini «{{...}}» qilib yuborishi mumkin
    assert _field({"car": "{{car_ref}}", "x": " 5 "}, "car", "x") == "5"
    assert lead_key(123) == -(10**13 + 123)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text_, **kw):
        self.sent.append((chat_id, text_, kw.get("reply_markup")))
        return SimpleNamespace(message_id=len(self.sent) + 100, chat=SimpleNamespace(id=chat_id))


@pytest.fixture
def mc_settings():
    patch = {"admin_telegram_ids": frozenset({ADMIN_ID}), "manychat_secret": SECRET, "manychat_api_key": ""}
    old = {k: getattr(config.settings, k) for k in patch}
    for k, v in patch.items():
        object.__setattr__(config.settings, k, v)
    yield
    for k, v in old.items():
        object.__setattr__(config.settings, k, v)


@pytest.fixture
async def factory(mc_settings):
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL berilmagan")
    await db_base.dispose_engine()
    f = db_base.init_engine(TEST_DB, pool_size=2, max_overflow=0)
    await db_base.create_tables()
    await apply_car_indexes(db_base.get_engine())
    async with db_base.get_engine().begin() as conn:
        await conn.execute(
            text("TRUNCATE instagram_posts, lead_relays, agent_messages, leads, car_events, cars RESTART IDENTITY CASCADE")
        )
    async with f() as s:
        repo = CarRepository(s)
        for p, status in (
            (ParsedCar(brand="Chevrolet", model="Cobalt", year=2020, mileage_km=98000, price_usd=9200, color="oq"), "active"),
            (ParsedCar(brand="Chevrolet", model="Cobalt", year=2019, mileage_km=120000, price_usd=8500, color="qora"), "active"),
            (ParsedCar(brand="Chevrolet", model="Spark", year=2015, mileage_km=150000, price_usd=6000), "sold"),
            (ParsedCar(brand="Chevrolet", model="Gentra", year=2022, mileage_km=40000, price_usd=12500), "active"),
        ):
            car = await repo.create_from_parsed(p, source=CarSource.CHANNEL, raw_text="")
            car.status = status
        await s.commit()
    yield f
    await db_base.dispose_engine()


async def test_match_caption(factory):
    async with factory() as s:
        cars = CarRepository(s)
        assert (await match_caption(cars, "Cobalt 2019 yil, 120 ming km, 8500$ 📞")).id == 2
        assert (await match_caption(cars, "Kobalt 2020 probeg 98 000 km")).id == 1
        assert await match_caption(cars, "Cobalt sotiladi, kelishamiz") is None  # ikkita Cobalt — taxmin yo'q
        assert (await match_caption(cars, "Spark 2015 yil 150 ming km")).status == CarStatus.SOLD
        assert (await match_caption(cars, "Yangi mashina! ID: 4")).id == 4
        assert await match_caption(cars, "Nexia 2018") is None


async def test_admin_choice_is_not_overwritten(factory):
    async with factory() as s:
        cars = CarRepository(s)
        await remember_post(cars, "ref1", car=await cars.get(1), source="admin")
        post = await remember_post(cars, "ref1", car=await cars.get(2))
        assert post.car_id == 1 and post.source == "admin"


def app_for(bridge: ManyChatBridge) -> web.Application:
    app = web.Application()
    bridge.add_routes(app)
    return app


def comment(text_: str, **extra) -> dict:
    return {"user_id": "555", "event": "comment", "text": text_, "ig_username": "aziz_ig", "name": "Aziz", **extra}


async def test_auth_and_validation(mc_settings):
    bridge = ManyChatBridge(FakeBot(), None)  # type: ignore[arg-type]
    async with TestClient(TestServer(app_for(bridge))) as client:
        assert (await client.post(MANYCHAT_PATH, json=comment("narxi?"))).status == 401
        bad = await client.post(MANYCHAT_PATH, json=comment("narxi?"), headers={"Authorization": "Bearer nope"})
        assert bad.status == 401
        assert (await client.post(MANYCHAT_PATH, json={"text": "x"}, headers=AUTH)).status == 400
        assert (await client.post(MANYCHAT_PATH, data="not json", headers=AUTH)).status == 400


async def test_comment_on_post_answers_about_that_car(factory):
    bot = FakeBot()
    async with TestClient(TestServer(app_for(ManyChatBridge(bot, factory)))) as client:
        caption = "Cobalt 2019 yil, 120 ming km, 8500$"
        r = await client.post(MANYCHAT_PATH, json=comment("narxi qancha?", media_id="1790001", caption=caption), headers=AUTH)
        body = await r.json()
        assert r.status == 200 and body["version"] == "v2" and body["car_id"] == 2
        assert "Cobalt 2019" in body["reply"] and "8 500" in body["reply"]
        assert body["content"]["messages"][0]["text"] == body["reply"]
        assert body["public_reply"]
        # Xuddi shu postga keyingi komment — caption'siz ham topiladi (bog'lanish saqlangan)
        r = await client.post(MANYCHAT_PATH, json=comment("probegi qancha?", media_id="1790001"), headers=AUTH)
        assert (await r.json())["car_id"] == 2
    async with factory() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        assert lead.channel == "manychat" and lead.telegram_id == lead_key(555) and lead.car_id == 2
        post = (await s.execute(select(InstagramPost))).scalar_one()
        assert post.ref == "1790001" and post.car_id == 2 and post.source == "auto"
    assert not any("qaysi mashina" in t for _, t, _ in bot.sent)


async def test_sold_car_post_says_sold_and_offers_similar(factory):
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        r = await client.post(
            MANYCHAT_PATH, json=comment("hali bormi?", post_url="https://instagram.com/p/SparkPost1/", car="3"), headers=AUTH
        )
        body = await r.json()
    assert body["car_id"] == 3 and "sotilgan" in body["reply"]


async def test_unknown_post_asks_admins_once_and_uses_their_choice(factory):
    bot = FakeBot()
    async with TestClient(TestServer(app_for(ManyChatBridge(bot, factory)))) as client:
        r = await client.post(MANYCHAT_PATH, json=comment("narxi qancha?", media_id="1790777"), headers=AUTH)
        assert (await r.json())["car_id"] is None
        await client.post(MANYCHAT_PATH, json=comment("kredit bormi?", media_id="1790777"), headers=AUTH)
        asks = [(chat, t, kb) for chat, t, kb in bot.sent if "qaysi mashina" in t]
        assert len(asks) == 1 and asks[0][0] == ADMIN_ID
        buttons = [b.callback_data for row in asks[0][2].inline_keyboard for b in row]
        assert "igpost:1:1" in buttons and "igpost:1:0" in buttons
        async with factory() as s:  # admin tugmani bosdi
            cars = CarRepository(s)
            await remember_post(cars, "1790777", car=await cars.get(4), source="admin")
            await s.commit()
        r = await client.post(MANYCHAT_PATH, json=comment("narxi qancha?", media_id="1790777"), headers=AUTH)
        body = await r.json()
    assert body["car_id"] == 4 and "Gentra" in body["reply"]


async def test_non_question_comment_is_silent(factory):
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        body = await (await client.post(MANYCHAT_PATH, json=comment("zo'r 🔥"), headers=AUTH)).json()
    assert body["reply"] == "" and body["content"]["messages"] == []


async def test_direct_message_with_model(factory):
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        body = await (
            await client.post(MANYCHAT_PATH, json={"user_id": "777", "event": "message", "text": "gentra bormi?"}, headers=AUTH)
        ).json()
    assert "Gentra" in body["reply"]


async def test_slow_answer_returns_in_time(mc_settings):
    object.__setattr__(config.settings, "manychat_reply_timeout", 0.05)
    bridge = ManyChatBridge(FakeBot(), None)  # type: ignore[arg-type]

    async def slow(_payload):
        await asyncio.sleep(1)
        return {"reply": "kech", "public_reply": "", "car_id": None, "images": []}

    bridge.answer = slow  # type: ignore[method-assign]
    try:
        async with TestClient(TestServer(app_for(bridge))) as client:
            body = await (await client.post(MANYCHAT_PATH, json=comment("narxi?"), headers=AUTH)).json()
        assert body["reply"] == manychat.LATE_FALLBACK_REPLY
    finally:
        object.__setattr__(config.settings, "manychat_reply_timeout", 8.0)
