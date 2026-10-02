"""Instagram webhook: imzo, Direct'ga agent javobi, egasi yozganda AI jim, kommentlar, HTTP qatlam."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select, text

from bot import config
from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import CarSource, Lead
from bot.instagram.client import InstagramClient, split_text
from bot.instagram.webhook import WEBHOOK_PATH, InstagramWebhook, verify_signature
from bot.services.car_parser import ParsedCar

TEST_DB = os.getenv("TEST_DATABASE_URL")
ADMIN_ID = 111
IG_USER = "17841400000000001"
OWN_ACCOUNT = "17841499999999999"
SECRET = "app-secret"


def test_signature():
    body = b'{"object":"instagram"}'
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, good, SECRET)
    assert not verify_signature(body + b" ", good, SECRET)
    assert not verify_signature(body, good, "")
    assert not verify_signature(body, None, SECRET)


def test_split_text():
    assert split_text("salom") == ["salom"]
    parts = split_text(("so'z " * 400).strip(), limit=1000)
    assert all(len(p) <= 1000 for p in parts) and len(parts) == 2


class FakeIG(InstagramClient):
    def __init__(self) -> None:
        super().__init__("token", version="v21.0")
        self.calls: list[tuple[str, str, dict | None]] = []
        self._n = 0

    async def _request(self, method, path, *, json=None, params=None):
        self.calls.append((method, path, json))
        if method == "GET":
            return {"username": "aziz_ig", "name": "Aziz"}
        self._n += 1
        return {"message_id": f"mid.{self._n}"}

    def sent_texts(self) -> list[str]:
        return [j["message"]["text"] for m, p, j in self.calls if p == "/me/messages" and "text" in j.get("message", {})]


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent) + 100, chat=SimpleNamespace(id=chat_id))


@pytest.fixture
def ig_settings():
    patch = {
        "admin_telegram_ids": frozenset({ADMIN_ID}),
        "ig_account_id": OWN_ACCOUNT,
        "ig_app_secret": SECRET,
        "ig_verify_token": "verify-me",
    }
    old = {k: getattr(config.settings, k) for k in patch}
    for k, v in patch.items():
        object.__setattr__(config.settings, k, v)
    yield
    for k, v in old.items():
        object.__setattr__(config.settings, k, v)


@pytest.fixture
async def factory(ig_settings):
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL berilmagan")
    await db_base.dispose_engine()
    f = db_base.init_engine(TEST_DB, pool_size=2, max_overflow=0)
    await db_base.create_tables()
    await apply_car_indexes(db_base.get_engine())
    async with db_base.get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE lead_relays, agent_messages, leads, car_events, cars RESTART IDENTITY CASCADE"))
    async with f() as s:
        await CarRepository(s).create_from_parsed(
            ParsedCar(brand="Chevrolet", model="Cobalt", year=2020, mileage_km=98000, price_usd=9200),
            source=CarSource.CHANNEL,
            raw_text="",
        )
        await s.commit()
    yield f
    await db_base.dispose_engine()


def dm(sender: str, text_: str | None = None, *, echo: bool = False, mid: str = "m1", recipient: str = OWN_ACCOUNT) -> dict:
    msg: dict = {"mid": mid}
    if text_ is not None:
        msg["text"] = text_
    if echo:
        msg["is_echo"] = True
    return {
        "object": "instagram",
        "entry": [{"messaging": [{"sender": {"id": sender}, "recipient": {"id": recipient}, "message": msg}]}],
    }


def comment(user: str, text_: str, cid: str = "c1") -> dict:
    value = {"id": cid, "text": text_, "from": {"id": user, "username": "aziz_ig"}, "media": {"id": "media1"}}
    return {"object": "instagram", "entry": [{"changes": [{"field": "comments", "value": value}]}]}


async def test_direct_message_gets_agent_reply_from_db(factory):
    ig, bot = FakeIG(), FakeBot()
    hook = InstagramWebhook(bot, factory, ig)
    await hook.process(dm(IG_USER, "kobalt bormi?"))
    assert any("Cobalt" in t for t in ig.sent_texts())
    async with factory() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        assert lead.channel == "instagram" and lead.telegram_id == int(IG_USER) and lead.username == "aziz_ig"


async def test_owner_reply_pauses_ai_and_forwards_to_manager(factory):
    ig, bot = FakeIG(), FakeBot()
    hook = InstagramWebhook(bot, factory, ig)
    await hook.process(dm(IG_USER, "salom"))
    our_mid = next(iter(ig.sent_mids))
    # O'zimiz yuborgan javobning echo'si — e'tiborsiz
    await hook.process(dm(OWN_ACCOUNT, "AI javobi", echo=True, mid=our_mid, recipient=IG_USER))
    async with factory() as s:
        assert (await s.execute(select(Lead))).scalar_one().human_until is None
    # Egasi Instagram ilovasidan o'zi yozdi
    await hook.process(dm(OWN_ACCOUNT, "Ha, keling", echo=True, mid="owner-1", recipient=IG_USER))
    async with factory() as s:
        assert (await s.execute(select(Lead))).scalar_one().human_until is not None
    before = len(ig.sent_texts())
    await hook.process(dm(IG_USER, "soat nechida?", mid="m2"))
    assert len(ig.sent_texts()) == before  # AI jim
    assert any(chat == ADMIN_ID and "soat nechida" in t for chat, t in bot.sent)


async def test_comment_question_public_and_private_reply(factory):
    ig, bot = FakeIG(), FakeBot()
    hook = InstagramWebhook(bot, factory, ig)
    await hook.process(comment(IG_USER, "zo'r 🔥"))
    assert ig.calls == []  # savol emas
    await hook.process(comment(OWN_ACCOUNT, "narxi qancha?"))
    assert ig.calls == []  # o'z kommentimiz
    await hook.process(comment(IG_USER, "kobalt narxi qancha? kredit bormi", cid="c9"))
    paths = [(m, p) for m, p, _ in ig.calls]
    assert ("POST", "/c9/replies") in paths
    private = [j for m, p, j in ig.calls if p == "/me/messages" and j["recipient"].get("comment_id") == "c9"]
    assert private and "Cobalt" in private[0]["message"]["text"]
    assert any("xarid niyati" in t for _, t in bot.sent)
    await hook.process(comment(IG_USER, "probegi qancha?", cid="c10"))
    assert ("POST", "/c10/replies") not in [(m, p) for m, p, _ in ig.calls]  # 10 daqiqalik cheklov


async def _noop(payload: dict) -> None:
    return None


async def test_http_verify_and_signature(ig_settings):
    hook = InstagramWebhook(FakeBot(), None, FakeIG())  # type: ignore[arg-type]
    hook.process = _noop  # type: ignore[method-assign]
    async with TestClient(TestServer(hook.app())) as client:
        ok = await client.get(
            WEBHOOK_PATH, params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"}
        )
        assert ok.status == 200 and await ok.text() == "42"
        bad = await client.get(WEBHOOK_PATH, params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"})
        assert bad.status == 403
        body = json.dumps({"object": "instagram", "entry": []}).encode()
        unsigned = await client.post(WEBHOOK_PATH, data=body)
        assert unsigned.status == 403
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        signed = await client.post(WEBHOOK_PATH, data=body, headers={"X-Hub-Signature-256": sig})
        assert signed.status == 200 and await signed.text() == "EVENT_RECEIVED"
