"""Butun bot oqimi: haqiqiy Dispatcher + routerlar tartibi + middleware, Telegram API soxta sessiya bilan.

Tekshiriladi: mijoz savoliga agent javobi, «Menejer» tugmasi → admin kartasi, admin reply → mijozga,
odam rejimida mijoz xabari → adminga, FSM oqimidagi foydalanuvchi matnini agent tortib olmasligi.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import CopyMessage, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageId, Update, User
from sqlalchemy import select, text

from bot import config
from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import AgentMessage, CarSource, Lead, LeadRelay, LeadStatus
from bot.handlers import register_handlers
from bot.handlers.ad_listing import AdListingStates
from bot.middlewares.database import DbSessionMiddleware
from bot.middlewares.workflow import BotUsernameMiddleware
from bot.services.car_parser import ParsedCar

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL berilmagan")

ADMIN_ID = 111
CUSTOMER_ID = 5005
BOT_ID = 42


class MockSession(BaseSession):
    """Telegram API o'rniga: so'rovlarni yozib boradi va to'g'ri turdagi javob qaytaradi."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []
        self._mid = 5000

    def _message(self, chat_id: int, text_: str | None = None) -> Message:
        self._mid += 1
        return Message(
            message_id=self._mid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            text=text_,
        )

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        self.requests.append(method)
        if isinstance(method, GetMe):
            return User(id=BOT_ID, is_bot=True, first_name="Bot", username="real_avto_test_bot")
        if isinstance(method, SendMessage):
            return self._message(int(method.chat_id), method.text)
        if isinstance(method, CopyMessage):
            self._mid += 1
            return MessageId(message_id=self._mid)
        if getattr(method, "__returning__", None) is bool:
            return True
        return self._message(int(getattr(method, "chat_id", 0) or 0))

    async def close(self) -> None:
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    def sent(self, method_type, chat_id: int | None = None) -> list:
        return [
            r for r in self.requests if isinstance(r, method_type) and (chat_id is None or int(r.chat_id) == chat_id)
        ]


class _CurrentSessionFactory:
    """Har test o'z engine'ini ochadi — middleware doim joriy session factory dan foydalanadi."""

    def __call__(self):
        return db_base.get_session_factory()()


_DP: tuple[Dispatcher, MemoryStorage] | None = None


def _dispatcher() -> tuple[Dispatcher, MemoryStorage]:
    """Routerlar modul darajasidagi singleton — Dispatcher bir marta quriladi."""
    global _DP
    if _DP is None:
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)
        dp["dispatcher"] = dp  # polling buni o'zi beradi; feed_update da qo'lda
        dp.update.middleware(BotUsernameMiddleware())
        dp.update.middleware(DbSessionMiddleware(_CurrentSessionFactory()))
        register_handlers(dp)
        _DP = (dp, storage)
    return _DP


@pytest.fixture
async def env():
    # Adminlar ro'yxati — barcha modullar bitta settings obyektini ishlatadi
    old_admins = config.settings.admin_telegram_ids
    object.__setattr__(config.settings, "admin_telegram_ids", frozenset({ADMIN_ID}))

    await db_base.dispose_engine()
    factory = db_base.init_engine(TEST_DB, pool_size=2, max_overflow=0)
    await db_base.create_tables()
    await apply_car_indexes(db_base.get_engine())
    async with db_base.get_engine().begin() as conn:
        await conn.execute(
            text("TRUNCATE lead_relays, agent_messages, leads, car_events, cars, wishlist, clients RESTART IDENTITY CASCADE")
        )
    async with factory() as s:
        await CarRepository(s).create_from_parsed(
            ParsedCar(brand="Chevrolet", model="Cobalt", year=2020, mileage_km=98000, price_usd=9200),
            source=CarSource.CHANNEL,
            raw_text="",
        )
        await s.commit()

    session = MockSession()
    bot = Bot("42:TEST", session=session)
    dp, storage = _dispatcher()
    try:
        yield dp, bot, session, factory, storage
    finally:
        object.__setattr__(config.settings, "admin_telegram_ids", old_admins)
        await db_base.dispose_engine()


_ids = iter(range(1, 100_000))


def _msg(user_id: int, text_: str, *, reply_to: int | None = None) -> Message:
    reply = None
    if reply_to is not None:
        reply = Message(message_id=reply_to, date=datetime.now(timezone.utc), chat=Chat(id=user_id, type="private"), text="x")
    return Message(
        message_id=next(_ids),
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Aziz" if user_id == CUSTOMER_ID else "Jasur"),
        text=text_,
        reply_to_message=reply,
    )


def _text_update(user_id: int, text_: str, *, reply_to: int | None = None) -> Update:
    return Update(update_id=next(_ids), message=_msg(user_id, text_, reply_to=reply_to))


def _callback_update(user_id: int, data: str) -> Update:
    return Update(
        update_id=next(_ids),
        callback_query=CallbackQuery(
            id=str(next(_ids)),
            from_user=User(id=user_id, is_bot=False, first_name="Aziz"),
            chat_instance="ci",
            data=data,
            message=_msg(user_id, "x"),
        ),
    )


async def test_customer_question_handoff_relay_and_human_mode(env):
    dp, bot, session, factory, _ = env

    # 1) Mijoz savoli → agent (AI kalitisiz rejim) bazadan javob beradi
    await dp.feed_update(bot, _text_update(CUSTOMER_ID, "kobalt bormi?"))
    replies = session.sent(SendMessage, CUSTOMER_ID)
    assert replies and "Cobalt" in replies[-1].text

    # 2) «Menejer bilan bog'lanish» → admin lead kartasini oladi
    await dp.feed_update(bot, _callback_update(CUSTOMER_ID, "agent:handoff"))
    cards = session.sent(SendMessage, ADMIN_ID)
    assert cards and "Lead #" in cards[-1].text
    async with factory() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        assert lead.status == LeadStatus.HANDED_OFF
        relay = (await s.execute(select(LeadRelay))).scalars().first()
    assert relay is not None and relay.admin_chat_id == ADMIN_ID

    # 3) Admin kartaga reply qiladi → javob mijozga ko'chiriladi, AI jim rejimga o'tadi
    await dp.feed_update(
        bot, _text_update(ADMIN_ID, "Assalomu alaykum, men menejer Jasur", reply_to=relay.admin_message_id)
    )
    assert len(session.sent(CopyMessage, CUSTOMER_ID)) == 1
    async with factory() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        assert lead.human_mode and lead.assigned_admin_id == ADMIN_ID

    # 4) Odam rejimida mijoz xabari → adminga uzatiladi, AI javob bermaydi
    before_customer = len(session.sent(SendMessage, CUSTOMER_ID))
    await dp.feed_update(bot, _text_update(CUSTOMER_ID, "Rahmat, bugun boraman"))
    assert len(session.sent(SendMessage, CUSTOMER_ID)) == before_customer
    assert "bugun boraman" in session.sent(SendMessage, ADMIN_ID)[-1].text
    async with factory() as s:
        roles = [m.role for m in (await s.execute(select(AgentMessage).order_by(AgentMessage.id))).scalars()]
    assert roles[:2] == ["user", "assistant"] and "admin" in roles and roles[-1] == "user"


async def test_admin_reply_to_unrelated_message_is_not_relayed(env):
    dp, bot, session, _, _ = env
    await dp.feed_update(bot, _text_update(ADMIN_ID, "salom", reply_to=999999))
    assert session.sent(CopyMessage) == []


async def test_agent_does_not_hijack_listing_form(env):
    dp, bot, _, factory, storage = env
    # Foydalanuvchi e'lon berish formasida (yil bosqichi) — matn agentga emas, formaga tegishli
    key = StorageKey(bot_id=BOT_ID, chat_id=CUSTOMER_ID, user_id=CUSTOMER_ID)
    await storage.set_state(key, AdListingStates.listing_year)
    try:
        await dp.feed_update(bot, _text_update(CUSTOMER_ID, "2019"))
        async with factory() as s:
            assert (await s.execute(select(Lead))).scalars().all() == []
    finally:
        await storage.set_state(key, None)
