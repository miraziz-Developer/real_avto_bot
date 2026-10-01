"""AI savdo agenti — soxta LLM (ssenariy) va haqiqiy PostgreSQL bilan.

Ishga tushirish: TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/realavto_test pytest tests/test_agent.py
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from bot.agent import tools as agent_tools
from bot.agent.fallback import fallback_reply
from bot.agent.prompt import build_system_prompt
from bot.agent.runner import FALLBACK_REPLY, run_agent
from bot.agent.tools import AgentContext
from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import CarSource, CarStatus, LeadRelay, LeadStatus
from bot.db.repositories import CrmRepository
from bot.services.car_parser import ParsedCar

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL berilmagan")

ADMIN_ID = 111
CUSTOMER_ID = 5005


@pytest.fixture
async def session_factory(monkeypatch):
    from bot.services import car_cards, lead_cards
    from bot.workers import lead_reminder

    patched = dataclasses.replace(lead_cards.settings, admin_telegram_ids=frozenset({ADMIN_ID}), lead_reminder_minutes=5)
    for mod in (lead_cards, car_cards, lead_reminder):
        monkeypatch.setattr(mod, "settings", patched)

    await db_base.dispose_engine()
    factory = db_base.init_engine(TEST_DB, pool_size=2, max_overflow=0)
    await db_base.create_tables()
    await apply_car_indexes(db_base.get_engine())
    async with db_base.get_engine().begin() as conn:
        await conn.execute(
            text("TRUNCATE lead_relays, agent_messages, leads, car_events, cars, wishlist, clients RESTART IDENTITY CASCADE")
        )
    yield factory
    await db_base.dispose_engine()


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, int, str]] = []
        self._mid = 1000

    def _msg(self, chat_id: int):
        self._mid += 1
        return SimpleNamespace(message_id=self._mid, chat=SimpleNamespace(id=chat_id))

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("text", chat_id, text))
        return self._msg(chat_id)

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        self.sent.append(("photo", chat_id, caption or ""))
        return self._msg(chat_id)

    async def send_media_group(self, chat_id, media, **kw):
        self.sent.append(("album", chat_id, str(len(media))))
        return [self._msg(chat_id) for _ in media]

    async def get_me(self):
        return SimpleNamespace(username="real_avto_test_bot")


class ScriptedLLM:
    """Har chaqiruvda navbatdagi tayyor javobni qaytaradi va unga kelgan xabarlarni saqlaydi."""

    def __init__(self, steps: list[dict]) -> None:
        self.steps = list(steps)
        self.calls: list[list[dict]] = []

    async def chat(self, messages, *, tools=None, model=None, temperature=0.3, max_tokens=700):
        self.calls.append([dict(m) for m in messages])
        return self.steps.pop(0)


def tool_call(name: str, args: dict, cid: str = "c1") -> dict:
    return {
        "content": None,
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


def final(text_: str) -> dict:
    return {"content": text_, "tool_calls": []}


async def _seed(s) -> dict[str, int]:
    cars = CarRepository(s)
    now = datetime.now(timezone.utc)

    async def mk(status=None, **kw):
        base = dict(brand="Chevrolet", model="Cobalt", year=2020, mileage_km=98000, price_usd=9200, confidence=0.9)
        base.update(kw)
        return await cars.create_from_parsed(
            ParsedCar(**base),
            source=CarSource.CHANNEL,
            raw_text="",
            photo_file_ids=["p1", "p2"],
            status=status,
            published_at=now - timedelta(days=3),
        )

    cobalt = await mk()
    sold_cobalt = await mk(status=CarStatus.SOLD, price_usd=8800)
    gentra = await mk(model="Gentra", year=2019, mileage_km=120000, price_usd=9800, transmission="avtomat")
    malibu = await mk(model="Malibu", year=2019, price_usd=21500, status=CarStatus.RESERVED)
    await s.commit()
    return {"cobalt": cobalt.id, "sold": sold_cobalt.id, "gentra": gentra.id, "malibu": malibu.id}


async def _ctx(s, bot) -> AgentContext:
    crm = CrmRepository(s)
    leads = LeadRepository(s)
    client = await crm.get_or_create_client(telegram_id=CUSTOMER_ID, full_name="Aziz")
    lead, _ = await leads.get_or_create_open(CUSTOMER_ID, name="Aziz", client_id=client.id)
    return AgentContext(bot=bot, chat_id=CUSTOMER_ID, lead=lead, cars=CarRepository(s), leads=leads, crm=crm)


async def test_search_by_slang_never_returns_sold(session_factory):
    async with session_factory() as s:
        ids = await _seed(s)
        ctx = await _ctx(s, FakeBot())
        out = json.loads(await ctx.execute("search_cars", {"model": "kobalt"}))
        assert [c["id"] for c in out["cars"]] == [ids["cobalt"]]
        out = json.loads(await ctx.execute("search_cars", {"model": "jentra", "budget_max_usd": 10000}))
        assert [c["id"] for c in out["cars"]] == [ids["gentra"]]
        # Bron mashina ko'rinadi, lekin «bron» deb belgilanadi
        out = json.loads(await ctx.execute("search_cars", {"model": "Malibu"}))
        assert out["cars"][0]["status"] == "bron"


async def test_search_no_match_returns_similar_by_budget(session_factory):
    async with session_factory() as s:
        ids = await _seed(s)
        ctx = await _ctx(s, FakeBot())
        out = json.loads(await ctx.execute("search_cars", {"model": "Spark", "budget_max_usd": 10000}))
        assert out["cars"] == []
        similar = {c["id"] for c in out["similar"]}
        assert similar and similar <= {ids["cobalt"], ids["gentra"]}


async def test_sold_car_details_offer_similar(session_factory):
    async with session_factory() as s:
        ids = await _seed(s)
        ctx = await _ctx(s, FakeBot())
        out = json.loads(await ctx.execute("get_car_details", {"car_id": ids["sold"]}))
        assert out["status"] == "sotilgan"
        assert ids["cobalt"] in [c["id"] for c in out["similar"]]


async def test_full_conversation_handoff_and_relay(session_factory):
    bot = FakeBot()
    async with session_factory() as s:
        ids = await _seed(s)
        ctx = await _ctx(s, bot)
        await ctx.leads.add_message(ctx.lead, "user", "Cobalt bormi? bugun 17:00 da ko'rgani boraman, raqamim 90 123 45 67")
        llm = ScriptedLLM(
            [
                tool_call("search_cars", {"model": "Cobalt"}),
                tool_call("send_car_photos", {"car_id": ids["cobalt"]}, "c2"),
                tool_call(
                    "update_customer_info",
                    {
                        "phone": "90 123 45 67",
                        "visit_time": "bugun 17:00",
                        "interested_car_id": ids["cobalt"],
                        "payment_method": "naqd",
                    },
                    "c3",
                ),
                tool_call("handoff_to_manager", {"reason": "ko'rishga keladi", "summary": "Cobalt 2020, bugun 17:00, naqd"}, "c4"),
                final("Ha, Cobalt 2020 bor — $9 200. **Menejerimiz** tez orada bog'lanadi."),
            ]
        )
        reply = await run_agent(ctx, llm)
        await s.commit()

        assert reply == "Ha, Cobalt 2020 bor — $9 200. Menejerimiz tez orada bog'lanadi."  # markdown tozalangan
        assert llm.calls[0][0]["role"] == "system" and llm.calls[0][-1]["role"] == "user"
        # Asbob natijasi modelga qaytgan va unda faqat sotuvdagi Cobalt bor
        tool_msgs = [m for m in llm.calls[1] if m["role"] == "tool"]
        assert [c["id"] for c in json.loads(tool_msgs[0]["content"])["cars"]] == [ids["cobalt"]]

        lead = ctx.lead
        assert lead.status == LeadStatus.HANDED_OFF
        assert lead.phone == "+998901234567"
        assert lead.car_id == ids["cobalt"] and lead.visit_time == "bugun 17:00"
        assert ("album", CUSTOMER_ID, "2") in bot.sent  # mijozga rasmlar
        card = next(t for _, chat, t in bot.sent if chat == ADMIN_ID)
        assert "ISSIQ MIJOZ" in card and "+998901234567" in card and "Cobalt" in card
        relays = (await s.execute(select(LeadRelay))).scalars().all()
        assert len(relays) == 1 and relays[0].admin_chat_id == ADMIN_ID
        assert (await ctx.leads.lead_by_relay(ADMIN_ID, relays[0].admin_message_id)).id == lead.id

        # Qayta topshirish — adminlarga ikkinchi karta yuborilmaydi
        before = len(bot.sent)
        out = json.loads(await ctx.execute("handoff_to_manager", {"reason": "x", "summary": "y"}))
        assert out["ok"] and len(bot.sent) == before


async def test_tool_errors_do_not_crash(session_factory):
    async with session_factory() as s:
        await _seed(s)
        ctx = await _ctx(s, FakeBot())
        assert "error" in json.loads(await ctx.execute("unknown_tool", {}))
        assert "error" in json.loads(await ctx.execute("search_cars", "not json"))
        assert "error" in json.loads(await ctx.execute("update_customer_info", {"phone": "123"}))
        assert "error" in json.loads(await ctx.execute("send_car_photos", {"car_id": 99999}))


async def test_runner_stops_after_max_steps(session_factory):
    async with session_factory() as s:
        await _seed(s)
        ctx = await _ctx(s, FakeBot())
        await ctx.leads.add_message(ctx.lead, "user", "salom")
        llm = ScriptedLLM([tool_call("search_cars", {}, f"c{i}") for i in range(10)])
        assert await run_agent(ctx, llm) == FALLBACK_REPLY


async def test_fallback_without_ai_lists_offerable(session_factory):
    async with session_factory() as s:
        ids = await _seed(s)
        ctx = await _ctx(s, FakeBot())
        reply, kb = await fallback_reply(ctx, "jentra bormi 10000$ gacha")
        assert "Gentra" in reply and "$9 800" in reply
        assert ctx.lead.car_id == ids["gentra"] and ctx.lead.budget_usd == 10000
        assert kb.inline_keyboard[0][0].callback_data == "agent:handoff"
        reply, _ = await fallback_reply(ctx, "salom")
        assert "Qanday mashina" in reply


async def test_save_search_alert_and_channel_car_notifies_once(session_factory):
    from bot.services.wishlist_notify import notify_wishlist_matches_car

    bot = FakeBot()
    async with session_factory() as s:
        await _seed(s)
        ctx = await _ctx(s, bot)
        out = json.loads(
            await ctx.execute("save_search_alert", {"brand": "Chevrolet", "model": "Spark", "budget_max_usd": 9000})
        )
        assert out["saved"]
        cars = CarRepository(s)
        spark = await cars.create_from_parsed(
            ParsedCar(brand="Chevrolet", model="Spark", year=2021, mileage_km=31000, price_usd=8500),
            source=CarSource.CHANNEL,
            raw_text="",
        )
        assert await notify_wishlist_matches_car(bot, ctx.crm, cars, spark) == 1
        assert any(chat == CUSTOMER_ID and "Spark" in t for _, chat, t in bot.sent)
        assert await notify_wishlist_matches_car(bot, ctx.crm, cars, spark) == 0  # ikkinchi marta yuborilmaydi


async def test_take_back_to_ai_and_reminder(session_factory):
    from bot.workers.lead_reminder import remind_once

    bot = FakeBot()
    async with session_factory() as s:
        await _seed(s)
        ctx = await _ctx(s, bot)
        assert await ctx.leads.hand_off(ctx.lead, reason="r", summary=None)
        ctx.lead.handed_off_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await s.commit()
    assert await remind_once(bot, session_factory) == 1
    assert any("daqiqadan beri hech kim olmadi" in t for _, _, t in bot.sent)
    assert await remind_once(bot, session_factory) == 0  # bir marta eslatiladi
    async with session_factory() as s:
        leads = LeadRepository(s)
        lead = await leads.get_open(CUSTOMER_ID)
        assert await leads.take(lead, ADMIN_ID)
        assert lead.human_mode and lead.status == LeadStatus.IN_PROGRESS
        assert not await leads.take(lead, 222)  # boshqa admin ololmaydi
        await leads.back_to_ai(lead)
        assert not lead.human_mode and lead.status == LeadStatus.ACTIVE


async def test_system_prompt_has_rules_and_context(session_factory):
    async with session_factory() as s:
        await _seed(s)
        ctx = await _ctx(s, FakeBot())
        ctx.lead.budget_usd = 10000
        p = build_system_prompt(ctx.lead)
        assert "FAQAT search_cars" in p
        assert "byudjet: $10000" in p
        # Ish vaqti sozlanmagan bo'lsa agent o'ylab topmasligi kerak
        assert "aniq aytma" in p


def test_phone_normalization():
    assert agent_tools._normalize_phone("90 123 45 67") == "+998901234567"
    assert agent_tools._normalize_phone("+998 (90) 123-45-67") == "+998901234567"
    assert agent_tools._normalize_phone("123") is None
