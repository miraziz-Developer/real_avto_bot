"""Butun bot oqimi: haqiqiy Dispatcher + routerlar tartibi + middleware, Telegram API soxta sessiya bilan.

Tekshiriladi: mijoz savoliga agent javobi, «Menejer» tugmasi → admin kartasi, admin reply → mijozga,
odam rejimida mijoz xabari → adminga, FSM oqimidagi foydalanuvchi matnini agent tortib olmasligi.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import CopyMessage, GetMe, SendMediaGroup, SendMessage
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    Chat,
    Message,
    MessageId,
    MessageOriginChannel,
    Update,
    User,
)
from sqlalchemy import select, text

from bot import config
from bot.db import base as db_base
from bot.db.cars_repo import CarRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import (
    AgentMessage,
    Car,
    CarSource,
    CarStatus,
    Lead,
    LeadRelay,
    LeadStatus,
    ListingSubmission,
    ListingSubmissionStatus,
)
from bot.db.repositories import CrmRepository
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


CHANNEL_REF = "@x"  # testlarda CHANNEL_ID=@x
CHANNEL_NUMERIC_ID = -1001234


def _cid(chat_id) -> int:
    """«@kanal» kabi username chat_id larni soxta raqamli id ga aylantirish."""
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return CHANNEL_NUMERIC_ID


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
            return self._message(_cid(method.chat_id), method.text)
        if isinstance(method, SendMediaGroup):
            return [self._message(_cid(method.chat_id)) for _ in method.media]
        if isinstance(method, CopyMessage):
            self._mid += 1
            return MessageId(message_id=self._mid)
        if getattr(method, "__returning__", None) is bool:
            return True
        return self._message(_cid(getattr(method, "chat_id", 0)))

    async def close(self) -> None:
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    def sent(self, method_type, chat_id: int | str | None = None) -> list:
        return [r for r in self.requests if isinstance(r, method_type) and (chat_id is None or r.chat_id == chat_id)]


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
            text(
                "TRUNCATE lead_relays, agent_messages, leads, car_events, cars, wishlist, clients, "
                "business_connections, channel_threads, listing_submissions RESTART IDENTITY CASCADE"
            )
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


# --- Telegram Business ------------------------------------------------------------------

OWNER_ID = 777
BIZ_CUSTOMER_ID = 6006


def _biz_msg(from_id: int, text_: str, *, from_bot: bool = False) -> Update:
    return Update(
        update_id=next(_ids),
        business_message=Message(
            message_id=next(_ids),
            date=datetime.now(timezone.utc),
            chat=Chat(id=BIZ_CUSTOMER_ID, type="private"),
            from_user=User(id=from_id, is_bot=False, first_name="Ikrom aka" if from_id == OWNER_ID else "Bobur"),
            text=text_,
            business_connection_id="bc1",
            sender_business_bot=User(id=BOT_ID, is_bot=True, first_name="Bot") if from_bot else None,
        ),
    )


async def test_business_chat_ai_replies_and_owner_takes_over(env):
    dp, bot, session, factory, _ = env
    await dp.feed_update(
        bot,
        Update(
            update_id=next(_ids),
            business_connection=BusinessConnection(
                id="bc1",
                user=User(id=OWNER_ID, is_bot=False, first_name="Ikrom aka"),
                user_chat_id=OWNER_ID,
                date=datetime.now(timezone.utc),
                can_reply=True,
                is_enabled=True,
            ),
        ),
    )
    assert "Business ulandi" in session.sent(SendMessage, ADMIN_ID)[-1].text

    # Mijoz egasining shaxsiy akkauntiga yozadi → AI egasi nomidan (business_connection_id bilan) javob beradi
    await dp.feed_update(bot, _biz_msg(BIZ_CUSTOMER_ID, "kobalt bormi?"))
    replies = session.sent(SendMessage, BIZ_CUSTOMER_ID)
    assert replies and replies[-1].business_connection_id == "bc1"
    assert "Cobalt" in replies[-1].text and replies[-1].reply_markup is None  # business'da tugmasiz
    async with factory() as s:
        lead = (await s.execute(select(Lead).where(Lead.telegram_id == BIZ_CUSTOMER_ID))).scalar_one()
        assert lead.channel == "business" and lead.business_connection_id == "bc1"

    # Botning o'zi yuborgan xabari qaytib kelsa — e'tiborsiz
    before = len(session.requests)
    await dp.feed_update(bot, _biz_msg(OWNER_ID, "AI javobi", from_bot=True))
    assert len(session.requests) == before

    # Egasi o'zi yozdi → AI shu mijoz bilan jim
    await dp.feed_update(bot, _biz_msg(OWNER_ID, "Ha, ertaga keling"))
    async with factory() as s:
        lead = (await s.execute(select(Lead).where(Lead.telegram_id == BIZ_CUSTOMER_ID))).scalar_one()
        assert lead.human_until is not None
    n_customer = len(session.sent(SendMessage, BIZ_CUSTOMER_ID))
    await dp.feed_update(bot, _biz_msg(BIZ_CUSTOMER_ID, "Soat nechida?"))
    assert len(session.sent(SendMessage, BIZ_CUSTOMER_ID)) == n_customer  # AI javob bermadi
    assert "Soat nechida" in session.sent(SendMessage, ADMIN_ID)[-1].text  # menejerga uzatildi


# --- Kanal kommentlari ----------------------------------------------------------------------

GROUP_ID = -100555
CHANNEL_CHAT = Chat(id=-1001234, type="channel", username="x")  # testlarda CHANNEL_ID=@x


async def test_channel_comments_answer_questions_from_db(env):
    dp, bot, session, factory, _ = env
    async with factory() as s:
        car = await CarRepository(s).create_from_parsed(
            ParsedCar(brand="Chevrolet", model="Gentra", year=2019, mileage_km=120000, price_usd=9800),
            source=CarSource.CHANNEL,
            raw_text="",
            channel_chat_id=CHANNEL_CHAT.id,
            channel_message_ids=[55, 56],
        )
        await s.commit()
        car_id = car.id

    # Kanal posti muhokama guruhiga avto-forward bo'ldi
    auto_id = next(_ids)
    await dp.feed_update(
        bot,
        Update(
            update_id=next(_ids),
            message=Message(
                message_id=auto_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=GROUP_ID, type="supergroup"),
                sender_chat=CHANNEL_CHAT,
                is_automatic_forward=True,
                forward_origin=MessageOriginChannel(date=datetime.now(timezone.utc), chat=CHANNEL_CHAT, message_id=55),
                text="Gentra 2019 ...",
            ),
        ),
    )

    def comment(uid: int, text_: str) -> Update:
        return Update(
            update_id=next(_ids),
            message=Message(
                message_id=next(_ids),
                date=datetime.now(timezone.utc),
                chat=Chat(id=GROUP_ID, type="supergroup"),
                from_user=User(id=uid, is_bot=False, first_name="Sardor"),
                text=text_,
                message_thread_id=auto_id,
            ),
        )

    await dp.feed_update(bot, comment(9001, "zo'r mashina 👍"))
    assert session.sent(SendMessage, GROUP_ID) == []  # savol emas — javob yo'q

    await dp.feed_update(bot, comment(9001, "narxi qancha?"))
    replies = session.sent(SendMessage, GROUP_ID)
    assert len(replies) == 1
    assert "hali sotuvda" in replies[0].text and "$9 800" in replies[0].text
    assert replies[0].reply_markup.inline_keyboard[0][0].url.endswith(f"start=car_{car_id}")

    await dp.feed_update(bot, comment(9001, "probegi qancha?"))
    assert len(session.sent(SendMessage, GROUP_ID)) == 1  # 10 daqiqalik cheklov

    # Xarid niyati → menejerga signal
    await dp.feed_update(bot, comment(9002, "kredit bilan olsam bo'ladimi?"))
    assert "xarid niyati" in session.sent(SendMessage, ADMIN_ID)[-1].text


# --- E'lon muzlatish va «Sotib olamiz» ------------------------------------------------------------

SELLER_ID = 8008


async def _pending_listing(factory, *, frozen_until: datetime | None = None, buyout_status: str | None = None) -> int:
    async with factory() as s:
        crm = CrmRepository(s)
        client = await crm.get_or_create_client(telegram_id=SELLER_ID, full_name="Sotuvchi")
        sub = await crm.create_listing_submission(
            client_id=client.id,
            user_telegram_id=SELLER_ID,
            seller_username="seller1",
            brand="Chevrolet",
            model="Malibu",
            year=2019,
            mileage=64000,
            condition_key="yaxshi",
            has_accident=False,
            price_ask_usd=21500,
            paint_status="toza",
            extra_details="",
            location="Toshkent",
            phone="+998901112233",
            photo_file_ids=["a1", "a2"],
            payment_screenshot_file_id=None,
        )
        sub.frozen_until = frozen_until or datetime.now(timezone.utc) + timedelta(hours=3)
        sub.buyout_status = buyout_status
        if buyout_status:
            sub.buyout_price_usd = 19000
        await s.commit()
        return sub.id


def _buttons(markup) -> list[str]:
    return [b.callback_data or b.url for row in (markup.inline_keyboard if markup else []) for b in row]


async def _offer(dp, bot, lid: int, price_text: str) -> None:
    await dp.feed_update(bot, _callback_update(ADMIN_ID, f"lad_b:{lid}"))
    await dp.feed_update(bot, _text_update(ADMIN_ID, price_text))


async def test_buyout_accepted_bought_and_posted_to_channel(env):
    dp, bot, session, factory, _ = env
    lid = await _pending_listing(factory)

    await _offer(dp, bot, lid, "8500")
    offer = session.sent(SendMessage, SELLER_ID)[-1]
    assert "$8,500" in offer.text and f"lbo:y:{lid}" in _buttons(offer.reply_markup)
    async with factory() as s:
        sub = await s.get(ListingSubmission, lid)
        assert sub.buyout_status == "offered" and sub.buyout_price_usd == 8500
        assert sub.frozen_until > datetime.now(timezone.utc) + timedelta(hours=20)  # sotuvchi javobi kutiladi

    # Begona odam taklif tugmasini bosa olmaydi
    await dp.feed_update(bot, _callback_update(CUSTOMER_ID, f"lbo:y:{lid}"))
    async with factory() as s:
        assert (await s.get(ListingSubmission, lid)).buyout_status == "offered"

    await dp.feed_update(bot, _callback_update(SELLER_ID, f"lbo:y:{lid}"))
    deal = session.sent(SendMessage, ADMIN_ID)[-1]
    assert "ROZI" in deal.text and "+998901112233" in deal.text
    assert f"lbo_done:{lid}" in _buttons(deal.reply_markup)

    await dp.feed_update(bot, _callback_update(ADMIN_ID, f"lbo_done:{lid}"))
    async with factory() as s:
        sub = await s.get(ListingSubmission, lid)
        assert sub.status == ListingSubmissionStatus.REJECTED and sub.buyout_status == "bought"
        car = (await s.execute(select(Car).where(Car.listing_submission_id == lid))).scalar_one()
        assert car.is_own and car.purchase_price_usd == 8500 and car.status == CarStatus.ARCHIVED
        car_id = car.id
    card = session.sent(SendMessage, ADMIN_ID)[-1]
    assert "Sotib olindi" in card.text and f"car:post:{car_id}" in _buttons(card.reply_markup)
    assert session.sent(SendMediaGroup, CHANNEL_REF) == []  # sotib olingan e'lon kanalga chiqmagan

    # Tayyor — bot o'zi kanalga joylaydi
    await dp.feed_update(bot, _callback_update(ADMIN_ID, f"car:post:{car_id}"))
    posts = session.sent(SendMediaGroup, CHANNEL_REF)
    assert len(posts) == 1 and "Malibu" in posts[0].media[0].caption
    async with factory() as s:
        car = await s.get(Car, car_id)
        assert car.status == CarStatus.ACTIVE and car.channel_message_ids


async def test_buyout_declined_publishes_listing(env):
    dp, bot, session, factory, _ = env
    lid = await _pending_listing(factory)
    await _offer(dp, bot, lid, "110 mln")
    async with factory() as s:
        assert (await s.get(ListingSubmission, lid)).buyout_status == "offered"
    await dp.feed_update(bot, _callback_update(SELLER_ID, f"lbo:n:{lid}"))
    assert len(session.sent(SendMediaGroup, CHANNEL_REF)) == 1
    async with factory() as s:
        sub = await s.get(ListingSubmission, lid)
        assert sub.status == ListingSubmissionStatus.APPROVED and sub.buyout_status == "declined"
        car = (await s.execute(select(Car).where(Car.listing_submission_id == lid))).scalar_one()
        assert car.status == CarStatus.ACTIVE and car.source == CarSource.BOT


async def test_freeze_worker_publishes_only_in_work_hours(env):
    from bot.services.work_hours import TASHKENT
    from bot.workers.listing_freeze import publish_due_once

    dp, bot, session, factory, _ = env
    day = datetime.now(TASHKENT).replace(hour=10, minute=0, second=0, microsecond=0)
    plain = await _pending_listing(factory, frozen_until=day - timedelta(minutes=1))
    offered = await _pending_listing(factory, frozen_until=day - timedelta(minutes=1), buyout_status="offered")
    future = await _pending_listing(factory, frozen_until=day + timedelta(hours=2))

    night = day.replace(hour=23)
    assert await publish_due_once(bot, factory, now=night) == 0
    assert await publish_due_once(bot, factory, now=day) == 2
    async with factory() as s:
        a, b, c = [await s.get(ListingSubmission, i) for i in (plain, offered, future)]
        assert a.status == ListingSubmissionStatus.APPROVED and a.auto_published
        assert b.status == ListingSubmissionStatus.APPROVED and b.buyout_status == "expired"
        assert c.status == ListingSubmissionStatus.PENDING
    assert "avtomatik kanalga chiqdi" in session.sent(SendMessage, ADMIN_ID)[-1].text
    assert await publish_due_once(bot, factory, now=day) == 0  # ikkinchi marta joylanmaydi


async def test_catalog_deep_links_start_sell_and_alert_flows(env):
    dp, bot, session, _, _ = env
    await dp.feed_update(bot, _text_update(CUSTOMER_ID, "/start sell"))
    msg = session.sent(SendMessage, CUSTOMER_ID)[-1]
    assert "sotmoqchimisiz" in msg.text and _buttons(msg.reply_markup) == ["ad_start"]
    await dp.feed_update(bot, _text_update(CUSTOMER_ID, "/start alert"))
    msg = session.sent(SendMessage, CUSTOMER_ID)[-1]
    assert _buttons(msg.reply_markup) == ["wishlist_start"]
