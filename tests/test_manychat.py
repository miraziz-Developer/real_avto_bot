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
            text("TRUNCATE instagram_comments, instagram_posts, lead_relays, agent_messages, leads, car_events, cars RESTART IDENTITY CASCADE")
        )
        await conn.execute(text("DELETE FROM app_meta WHERE key = 'ig_poll_state'"))
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


class FakeCommentAI:
    """Komment AI: matnga qarab toifa qaytaradi, kelgan payload va promptni eslab qoladi."""

    enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def chat_json(self, system, user, **kw):
        import json as _json

        payload = _json.loads(user)
        self.calls.append((system, payload))
        t = payload["xabar"]
        if "aldov" in t:
            return {"action": "reply", "category": "negative", "reply": "Uzr, menejerimiz aniqlab beradi.", "admin_note": "narxdan norozi"}
        if "ahmoq" in t:
            return {"action": "reply", "category": "toxic", "reply": "x"}
        if "zo'r" in t:
            return {"action": "reply", "category": "praise", "reply": "Rahmat! 🙏"}
        return {"action": "reply", "category": "question", "reply": "Narxi $8 500, batafsil Direct'da.", "buy_intent": False}


class AllowAll:
    async def allow_user(self, key):
        return True


async def test_comment_ai_categories(factory, monkeypatch):
    ai = FakeCommentAI()
    monkeypatch.setattr(manychat, "get_ai", lambda: ai)
    monkeypatch.setattr(manychat, "get_budget", lambda: AllowAll())
    bot = FakeBot()
    caption = "Cobalt 2019 yil, 120 ming km, 8500$"
    async with TestClient(TestServer(app_for(ManyChatBridge(bot, factory)))) as client:

        async def send(text_: str, user: str) -> dict:
            body = comment(text_, media_id="1790001", caption=caption, user_id=user)
            return await (await client.post(MANYCHAT_PATH, json=body, headers=AUTH)).json()

        q = await send("narxi qancha?", "1")
        assert q["public_reply"] == "Narxi $8 500, batafsil Direct'da." and q["car_id"] == 2
        assert "Cobalt 2019" in q["reply"]  # Direct: savdo agenti (AI'siz — bazadan) javobi
        system, payload = ai.calls[0]
        assert "INSTAGRAM" in system and payload["shu_post_mashinasi"]["id"] == 2

        neg = await send("juda qimmat, aldov", "2")
        assert neg["public_reply"] == "Uzr, menejerimiz aniqlab beradi." and neg["reply"] == ""
        praise = await send("zo'r mashina", "3")
        assert praise["public_reply"] == "Rahmat! 🙏" and praise["reply"] == ""
        toxic = await send("ahmoq", "4")
        assert toxic["public_reply"] == "" and toxic["reply"] == ""
    alerts = [t for _, t, _ in bot.sent]
    assert any("salbiy fikr" in t and "#2" in t and "narxdan norozi" in t for t in alerts)
    assert any("haqorat" in t for t in alerts)
    async with factory() as s:  # faqat Direct yozilganlar lead bo'ladi
        assert len((await s.execute(select(Lead))).scalars().all()) == 1


class FakeIGMedia:
    enabled = True

    def __init__(self, media: list[dict]) -> None:
        self.media = media
        self.downloads: list[str] = []

    async def list_media(self, limit=15):
        return self.media

    async def download(self, url, *, max_bytes):
        self.downloads.append(url)
        return b"video-bytes", "video/mp4"


class FakeVideoAI:
    enabled = True
    supports_video = True

    async def transcribe(self, data, *, filename, mime_type):
        return "Assalomu alaykum, Gentra 2022 yil, 40 ming km yurgan, oq rang"

    async def chat_json(self, system, user, **kw):
        return {}


async def test_ig_sync_links_new_posts_by_caption_and_video(factory, monkeypatch):
    from bot.workers import ig_sync

    monkeypatch.setattr(ig_sync, "get_ai", lambda: FakeVideoAI())
    monkeypatch.setattr(ig_sync, "get_budget", lambda: AllowAll())
    now = "2099-01-01T10:00:00+0000"
    ig = FakeIGMedia(
        [
            {"id": "1791", "caption": "Cobalt 2019 yil 120 ming km 8500$", "media_type": "REELS", "media_url": "https://cdn/1", "timestamp": now},
            {"id": "1792", "caption": "Yangi kelgan mashina 🔥", "media_type": "REELS", "media_url": "https://cdn/2", "timestamp": now,
             "permalink": "https://instagram.com/reel/m2code/"},
            {"id": "1793", "caption": "Bayramingiz bilan!", "media_type": "IMAGE", "timestamp": now},
        ]
    )
    bot = FakeBot()
    assert await ig_sync.sync_once(bot, factory, ig) == 2
    assert ig.downloads == ["https://cdn/2"]  # matndan topilgani uchun 1-videoni ko'rmaydi
    async with factory() as s:
        posts = {p.ref: p for p in (await s.execute(select(InstagramPost))).scalars()}
    assert posts["1791"].car_id == 2 and posts["1792"].car_id == 4 and posts["1793"].car_id is None
    assert "[Video]: Assalomu alaykum, Gentra" in posts["1792"].caption
    texts = [t for _, t, _ in bot.sent]
    assert any("#4" in t and "video bo'yicha" in t for t in texts)
    assert sum("qaysi mashina" in t for t in texts) == 1  # faqat 1793 so'raladi
    # Qayta ishga tushganda — hech narsa takrorlanmaydi
    assert await ig_sync.sync_once(bot, factory, ig) == 0 and len(ig.downloads) == 1
    # Komment shu reel ostiga keldi — mashina allaqachon ma'lum
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        body = await (await client.post(MANYCHAT_PATH, json=comment("narxi qancha?", media_id="1792"), headers=AUTH)).json()
    assert body["car_id"] == 4


async def test_without_post_info_asks_which_car_then_uses_code(factory):
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        # ManyChat post ID'sini bermadi — taxmin qilmaymiz, ro'yxat bilan so'raymiz
        body = await (await client.post(MANYCHAT_PATH, json=comment("narxi qancha?"), headers=AUTH)).json()
        assert body["car_id"] is None and "Raqamini yozing" in body["reply"] and "2 — Chevrolet Cobalt 2019" in body["reply"]
        assert "Spark" not in body["reply"]  # sotilgani ro'yxatda yo'q
        # Mijoz Direct'da raqamni yozdi
        dm = {"user_id": "555", "event": "message", "text": "2"}
        body = await (await client.post(MANYCHAT_PATH, json=dm, headers=AUTH)).json()
        assert body["car_id"] == 2
        # Keyingi savol — o'sha mashina haqida
        dm["text"] = "probegi qancha?"
        body = await (await client.post(MANYCHAT_PATH, json=dm, headers=AUTH)).json()
        assert "Cobalt 2019" in body["reply"] and "120 000" in body["reply"]
        # Reel matnidagi kodni kommentga yozsa ham bo'ladi
        body = await (await client.post(MANYCHAT_PATH, json=comment("kod 4 narxi qancha?", user_id="9"), headers=AUTH)).json()
        assert body["car_id"] == 4 and "Gentra" in body["reply"]


async def test_make_mode_public_answer_own_reply_and_video(factory, monkeypatch):
    from bot.workers import ig_sync

    async def fake_download(url, *, max_bytes):
        return b"v", "video/mp4"

    monkeypatch.setattr(ig_sync, "get_ai", lambda: FakeVideoAI())
    monkeypatch.setattr(ig_sync, "get_budget", lambda: AllowAll())
    monkeypatch.setattr(ig_sync, "download_url", fake_download)
    object.__setattr__(config.settings, "instagram_username", "realavto")
    bot = FakeBot()
    bridge = ManyChatBridge(bot, factory)
    try:
        async with TestClient(TestServer(app_for(bridge))) as client:
            make = {"user_id": "17841400000000777", "event": "comment", "ig_username": "aziz_ig", "dm": "false"}
            # O'zimizning javobimiz qayta keldi — jim
            own = await (await client.post("/webhooks/make", json={**make, "ig_username": "realavto", "text": "narxi?"}, headers=AUTH)).json()
            assert own["public_reply"] == "" and own["reply"] == ""
            # Post matni hech narsa demaydi, lekin video bor — javob ochiq, «Direct'ga» demaydi
            body = {**make, "text": "narxi qancha?", "media_id": "1795", "caption": "Yangi kelgan 🔥",
                    "media_url": "https://cdn/1795"}
            r = await (await client.post("/webhooks/make", json=body, headers=AUTH)).json()
            assert r["reply"] == "" and "Direct" not in r["public_reply"] and "Raqamini yozing" in r["public_reply"]
            await asyncio.gather(*list(bridge._tasks))  # fonda video ko'rildi
            r = await (await client.post("/webhooks/make", json={**body, "text": "probegi qancha?"}, headers=AUTH)).json()
            assert r["car_id"] == 4 and "Gentra" in r["public_reply"]
    finally:
        object.__setattr__(config.settings, "instagram_username", "x")
    assert any("video bo'yicha" in t for _, t, _ in bot.sent)


async def test_form_fields_are_accepted(factory):
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        form = {"user_id": "12345", "event": "comment", "dm": "false", "text": 'gentra "narxi" qancha?'}
        body = await (await client.post("/webhooks/make", data=form, headers=AUTH)).json()
    assert body["car_id"] == 4 and "Gentra" in body["public_reply"] and body["reply"] == ""


async def test_make_secret_derived_from_bot_token(factory):
    from bot.instagram.manychat import make_secret

    key = make_secret()
    assert len(key) == 40 and key != SECRET
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        form = {"user_id": "12345", "event": "comment", "dm": "false", "text": "gentra narxi?"}
        ok = await client.post("/webhooks/make", data=form, headers={"Authorization": f"Bearer {key}"})
        assert ok.status == 200
        bad = await client.post("/webhooks/make", data=form, headers={"Authorization": f"Bearer {key[:-1]}x"})
        assert bad.status == 401


async def test_username_only_comment_gets_stable_key(factory):
    from bot.instagram.manychat import subscriber_key

    a = subscriber_key({"ig_username": "@Aziz_IG"})
    assert a == subscriber_key({"ig_username": "aziz_ig"}) and a.isdigit() and int(a) < 10**16
    assert subscriber_key({}) == ""
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        form = {"user_id": "", "ig_username": "aziz_ig", "event": "comment", "dm": "false", "text": "gentra narxi?"}
        body = await (await client.post("/webhooks/make", data=form, headers=AUTH)).json()
    assert body["car_id"] == 4


def test_emoji_only_comments_are_detected_and_answered_by_kind():
    import random

    from bot.services.emoji_reply import emoji_reply, is_emoji_only, reaction_kind

    assert is_emoji_only("❤️") and is_emoji_only("🔥🔥🔥") and is_emoji_only(" 👍🏻 ") and is_emoji_only("😂😂")
    assert not is_emoji_only("zo'r 🔥") and not is_emoji_only("5") and not is_emoji_only("") and not is_emoji_only("?")
    assert reaction_kind("😍") == "love" and reaction_kind("👍🏻") == "fire" and reaction_kind("😂") == "funny"
    assert reaction_kind("😡👎") == "bad" and reaction_kind("😢") == "sad" and reaction_kind("😱") == "wow"
    rnd = random.Random(1)
    assert "Fikringiz" in emoji_reply("👎", rnd=rnd) or "fikringiz" in emoji_reply("👎", rnd=rnd)
    assert emoji_reply("❤️", rnd=rnd).startswith("Rahmat")


async def test_emoji_comment_gets_short_reply_once_without_ai(factory):
    async with TestClient(TestServer(app_for(bridge := ManyChatBridge(FakeBot(), factory)))) as client:
        base = {"user_id": "555", "event": "comment", "dm": "false", "media_id": "1796", "caption": "Gentra ID: 4"}
        r = await (await client.post("/webhooks/make", json={**base, "text": "❤️🔥"}, headers=AUTH)).json()
        assert r["car_id"] == 4 and r["public_reply"] and r["reply"] == ""
        assert "sotuvda" not in r["public_reply"]  # AI'siz zaxira javob mashina holatini taxmin qilmaydi
        again = await (await client.post("/webhooks/make", json={**base, "text": "🔥"}, headers=AUTH)).json()
        assert again["public_reply"] == ""  # bir odamga bir postda bir marta
        bad = await (await client.post("/webhooks/make", json={**base, "user_id": "556", "text": "👎"}, headers=AUTH)).json()
        assert "ikr" in bad["public_reply"].lower() or "🙏" in bad["public_reply"]
    assert not bridge.bot.sent  # adminlarga xabar yo'q


async def test_emoji_comment_answered_by_ai_with_car_status(factory, monkeypatch):
    ai = FakeCommentAI()
    monkeypatch.setattr(manychat, "get_ai", lambda: ai)
    monkeypatch.setattr(manychat, "get_budget", lambda: AllowAll())
    async with TestClient(TestServer(app_for(ManyChatBridge(FakeBot(), factory)))) as client:
        body = {"user_id": "777", "event": "comment", "dm": "false", "media_id": "1797", "caption": "Spark ID: 3", "text": "😍"}
        r = await (await client.post("/webhooks/make", json=body, headers=AUTH)).json()
    assert r["car_id"] == 3 and r["public_reply"] == "Narxi $8 500, batafsil Direct'da."  # AI javobi ishlatildi
    system, payload = ai.calls[0]
    assert "Faqat emoji" in system and payload["shu_post_mashinasi"]["id"] == 3  # AI mashina (sotilgan Spark) faktini ko'radi


async def test_make_poll_media_then_comments_once(factory):
    from datetime import UTC, datetime, timedelta

    from aiohttp import web as _web

    from bot.instagram.make_poll import FIELD_SEP as F, POLL_PATH, ROW_SEP as R, MakePoller

    bridge = ManyChatBridge(FakeBot(), factory)
    app = _web.Application()
    MakePoller(bridge, factory).add_routes(app)
    object.__setattr__(config.settings, "instagram_username", "real_avto_arzon")

    def media_rows(count: int) -> str:
        return R.join([
            F.join(["1801", str(count), "https://instagram.com/reel/AAA111/", "VIDEO", "https://cdn/1801", "Gentra ID: 4"]),
            F.join(["1802", "0", "https://instagram.com/p/BBB222/", "IMAGE", "", "Nasiya savdosi"]),
        ])

    try:
        async with TestClient(TestServer(app)) as client:
            async def post(data):
                return await (await client.post(POLL_PATH, data=data, headers=AUTH)).json()

            from urllib.parse import quote

            # Make «raw» matn (application/octet-stream / text/plain) — qiymatlar encodeURL bilan
            raw = f"stage=media&rows={quote(media_rows(2))}"
            first = await client.post(POLL_PATH, data=raw.encode(), headers={**AUTH, "Content-Type": "application/octet-stream"})
            assert (await first.json())["check"] == []  # birinchi — faqat eslab qoladi
            assert (await post({"stage": "media", "rows": media_rows(2)}))["check"] == []  # o'zgarish yo'q
            assert (await post({"stage": "media", "rows": media_rows(5)}))["check"] == ["1801"]
            now = datetime.now(UTC)
            old = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S+0000")
            new = (now + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S+0000")
            rows = R.join([
                F.join(["c1", old, "eski_mijoz", "11", "1801", "narxi qancha?"]),
                F.join(["c2", new, "aziz_ig", "12", "1801", "gentra narxi qancha?"]),
                F.join(["c3", new, "real_avto_arzon", "13", "1801", "Rahmat!"]),
            ])
            r = await post({"stage": "comments", "media_id": "", "rows": rows})  # post ID qatordan olinadi
            assert [x["comment_id"] for x in r["replies"]] == ["c2"] and "Gentra" in r["replies"][0]["message"]
            # Qayta kelsa — javob yozilmaydi; bizning javobimiz kommentlar sonini oshirgani tekshiruvga sabab bo'lmaydi
            assert (await post({"stage": "comments", "media_id": "1801", "rows": rows}))["replies"] == []
            assert (await post({"stage": "media", "rows": media_rows(6)}))["check"] == []
            bad = await client.post(POLL_PATH, data={"stage": "media"}, headers={"Authorization": "Bearer nope"})
            assert bad.status == 401
    finally:
        object.__setattr__(config.settings, "instagram_username", "x")
    r = await bridge.answer({"user_id": "12", "event": "comment", "dm": "false", "text": "gentra narxi qancha?", "comment_id": "c2"})
    assert r["public_reply"] == ""  # webhook orqali kelsa ham — allaqachon javob berilgan


async def test_profanity_comment_gets_no_public_reply_but_admins_know(factory):
    bot = FakeBot()
    async with TestClient(TestServer(app_for(ManyChatBridge(bot, factory)))) as client:
        body = {"user_id": "901", "event": "comment", "dm": "false", "text": "gentra buncha qimmat blee gandon?", "ig_username": "troll"}
        r = await (await client.post("/webhooks/make", json=body, headers=AUTH)).json()
    assert r["public_reply"] == "" and r["reply"] == ""
    assert any("haqorat" in t and "@troll" in t for _, t, _ in bot.sent)
