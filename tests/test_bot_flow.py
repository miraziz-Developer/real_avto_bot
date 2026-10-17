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


# --- Review tuzatishlari uchun regression testlar ---------------------------------------------


async def test_expired_offer_is_retried_and_stale_deals_reminded(env):
    from bot.services.work_hours import TASHKENT
    from bot.workers.listing_freeze import publish_due_once, remind_stale_deals_once

    dp, bot, session, factory, _ = env
    day = datetime.now(TASHKENT).replace(hour=11, minute=0, second=0, microsecond=0)
    # Avvalgi urinishda kanal xatosi bo'lgan (expired) e'lon — qayta urinishda chiqishi kerak
    expired = await _pending_listing(factory, frozen_until=day - timedelta(minutes=1), buyout_status="expired")
    accepted = await _pending_listing(factory, frozen_until=day - timedelta(minutes=1), buyout_status="accepted")
    assert await publish_due_once(bot, factory, now=day) == 1
    async with factory() as s:
        assert (await s.get(ListingSubmission, expired)).status == ListingSubmissionStatus.APPROVED
        assert (await s.get(ListingSubmission, accepted)).status == ListingSubmissionStatus.PENDING  # bizga sotmoqchi

    # Rozi bo'lgan, lekin hal qilinmagan kelishuv — adminlarga eslatma, kuniga bir marta
    assert await remind_stale_deals_once(bot, factory, now=day) == 1
    reminder = session.sent(SendMessage, ADMIN_ID)[-1]
    assert "Hal qilinmagan kelishuv" in reminder.text and f"lbo_done:{accepted}" in _buttons(reminder.reply_markup)
    assert await remind_stale_deals_once(bot, factory, now=day) == 0


async def test_closed_lead_card_buttons_do_not_reopen(env):
    dp, bot, session, factory, _ = env
    await dp.feed_update(bot, _text_update(CUSTOMER_ID, "kobalt bormi?"))
    async with factory() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        lead.status = LeadStatus.WON
        await s.commit()
        lead_id = lead.id
    await dp.feed_update(bot, _callback_update(ADMIN_ID, f"lead:take:{lead_id}"))
    async with factory() as s:
        lead = await s.get(Lead, lead_id)
        assert lead.status == LeadStatus.WON and not lead.human_mode


async def test_channel_edit_completing_car_notifies_saved_searches(env):
    dp, bot, session, factory, _ = env
    async with factory() as s:
        crm = CrmRepository(s)
        client = await crm.get_or_create_client(telegram_id=CUSTOMER_ID, full_name="Aziz")
        await crm.create_wishlist(
            client_id=client.id, brand="Chevrolet", model="Spark", year_min=2015, year_max=2030,
            budget_min=None, budget_max=9000, condition_key=None,
        )
        await s.commit()
    post_id = next(_ids)

    def channel_msg(text_: str) -> Message:
        return Message(message_id=post_id, date=datetime.now(timezone.utc), chat=CHANNEL_CHAT, text=text_)

    await dp.feed_update(bot, Update(update_id=next(_ids), channel_post=channel_msg("Spark 2021 keldi, 31000 km")))
    async with factory() as s:
        car = (await s.execute(select(Car).where(Car.model == "Spark"))).scalar_one()
        assert car.status == CarStatus.ACTIVE and car.price_usd is None  # narxsiz ham sotuvda
    await dp.feed_update(
        bot, Update(update_id=next(_ids), edited_channel_post=channel_msg("Spark 2021 keldi, 31000 km, narxi 8500$"))
    )
    async with factory() as s:
        car = (await s.execute(select(Car).where(Car.model == "Spark"))).scalar_one()
        assert car.status == CarStatus.ACTIVE and car.price_usd == 8500
    assert any("Siz qidirgan mashina" in m.text for m in session.sent(SendMessage, CUSTOMER_ID))


async def test_published_bot_listing_is_not_renotified_via_car(env):
    from bot.services.wishlist_notify import notify_wishlist_matches_car

    dp, bot, session, factory, _ = env
    lid = await _pending_listing(factory)
    await _offer(dp, bot, lid, "8500")
    await dp.feed_update(bot, _callback_update(SELLER_ID, f"lbo:n:{lid}"))  # rad → e'lon kanalga
    async with factory() as s:
        car = (await s.execute(select(Car).where(Car.listing_submission_id == lid))).scalar_one()
        cars = CarRepository(s)
        assert await cars.has_event(car, "wishlist_notified")
        assert await notify_wishlist_matches_car(bot, CrmRepository(s), cars, car) == 0


def test_instagram_lead_card_links_to_instagram_not_telegram():
    from bot.db.models import Lead as LeadModel
    from bot.services.lead_cards import lead_card_html

    lead = LeadModel(id=7, telegram_id=17841400000000001, channel="instagram", username="aziz_ig", name="Aziz", status="handed_off")
    html_text = lead_card_html(lead)
    assert "https://instagram.com/aziz_ig" in html_text and "tg://user" not in html_text


class _FakeVideoAI:
    """Gemini o'rniga: videoni «ko'radi», agent javobini qaytaradi."""

    provider = "gemini"
    supports_video = True
    model = agent_model = "fake"
    enabled = True

    def __init__(self) -> None:
        self.media: list[tuple[int, str | None]] = []
        self.chats: list[list[dict]] = []

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg", mime_type: str | None = None) -> str:
        self.media.append((len(audio), mime_type))
        return "kobalt bormi, narxi qancha?\n[Videoda ko'rinadi]: oq Chevrolet Cobalt"

    async def chat(self, messages, *, tools=None, model=None, temperature=0.3, max_tokens=700):
        self.chats.append(messages)
        return {"content": "Ha, Cobalt 2020 bor — 9200$.", "tool_calls": []}

    async def chat_json(self, system, user, **kw):
        return {}

    async def close(self) -> None:
        pass


async def test_customer_video_is_seen_by_ai_and_answered(env, monkeypatch):
    from aiogram.types import File, Video

    from bot.agent import service as agent_service
    from bot.handlers import sales_agent

    dp, bot, session, factory, _ = env
    fake = _FakeVideoAI()
    monkeypatch.setattr(sales_agent, "get_ai", lambda: fake)
    monkeypatch.setattr(agent_service, "get_ai", lambda: fake)

    async def get_file(file_id, **kw):
        return File(file_id=file_id, file_unique_id="u", file_path="videos/v.mp4")

    async def download_file(path, **kw):
        import io

        return io.BytesIO(b"\x00" * 1234)

    monkeypatch.setattr(bot, "get_file", get_file)
    monkeypatch.setattr(bot, "download_file", download_file)

    video_msg = Message(
        message_id=next(_ids),
        date=datetime.now(timezone.utc),
        chat=Chat(id=CUSTOMER_ID, type="private"),
        from_user=User(id=CUSTOMER_ID, is_bot=False, first_name="Aziz"),
        video=Video(
            file_id="vid1", file_unique_id="uv1", width=720, height=1280, duration=30,
            mime_type="video/mp4", file_size=1234,
        ),
    )
    await dp.feed_update(bot, Update(update_id=next(_ids), message=video_msg))

    assert fake.media == [(1234, "video/mp4")]
    # Agent videodagi savolni oldi va javob berdi
    assert any("[Video]: kobalt bormi" in (m.get("content") or "") for m in fake.chats[-1] if m["role"] == "user")
    assert "Cobalt 2020 bor" in session.sent(SendMessage, CUSTOMER_ID)[-1].text
    async with factory() as s:
        contents = [m.content for m in (await s.execute(select(AgentMessage).order_by(AgentMessage.id))).scalars()]
    assert any("[Videoda ko'rinadi]: oq Chevrolet Cobalt" in c for c in contents)


async def test_customer_video_too_big_goes_to_manager_only(env, monkeypatch):
    from aiogram.types import Video

    from bot.handlers import sales_agent

    dp, bot, session, _, _ = env
    fake = _FakeVideoAI()
    monkeypatch.setattr(sales_agent, "get_ai", lambda: fake)
    video_msg = Message(
        message_id=next(_ids),
        date=datetime.now(timezone.utc),
        chat=Chat(id=CUSTOMER_ID, type="private"),
        from_user=User(id=CUSTOMER_ID, is_bot=False, first_name="Aziz"),
        video=Video(
            file_id="big", file_unique_id="ub", width=720, height=1280, duration=300,
            mime_type="video/mp4", file_size=50 * 1024 * 1024,
        ),
    )
    await dp.feed_update(bot, Update(update_id=next(_ids), message=video_msg))
    assert fake.media == []  # 20 MB dan katta — Telegram bermaydi, urinmaymiz
    assert "Menejerimiz ko'rib chiqadi" in session.sent(SendMessage, CUSTOMER_ID)[-1].text


async def test_admin_overview_and_queue_commands(env):
    dp, bot, session, factory, _ = env
    # Oddiy foydalanuvchi — javob yo'q
    await dp.feed_update(bot, _text_update(CUSTOMER_ID, "/umumiy"))
    assert not any("umumiy statistika" in (m.text or "") for m in session.sent(SendMessage, CUSTOMER_ID))

    await dp.feed_update(bot, _text_update(ADMIN_ID, "/umumiy"))
    text_ = session.sent(SendMessage, ADMIN_ID)[-1].text
    assert "umumiy statistika" in text_ and "AI o'chiq" in text_

    await dp.feed_update(bot, _text_update(ADMIN_ID, "/navbat"))
    assert "navbati bo'sh" in session.sent(SendMessage, ADMIN_ID)[-1].text


# --- Kommentlar: AI rejimi -------------------------------------------------------------------
class _FakeCommentAI:
    provider = "gemini"
    supports_video = True
    model = agent_model = "fake"
    enabled = True

    def __init__(self, decisions: list[dict] | None = None, fail: bool = False) -> None:
        self.decisions = list(decisions or [])
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def chat_json(self, system, user, **kw):
        from bot.ai import AIError

        self.calls.append((system, user))
        if self.fail:
            raise AIError("tarmoq")
        return self.decisions.pop(0)

    async def chat(self, *a, **kw):
        return {"content": "", "tool_calls": []}

    async def transcribe(self, audio, *, filename="audio.ogg", mime_type=None):
        return "narxi qancha"

    async def close(self):
        pass


async def _comment_setup(factory, dp, bot, *, status=None):
    async with factory() as s:
        car = await CarRepository(s).create_from_parsed(
            ParsedCar(brand="Chevrolet", model="Gentra", year=2019, mileage_km=120000, price_usd=9800),
            source=CarSource.CHANNEL,
            raw_text="Gentra 2019, 120 000 km\n[Ovoz]: narxi 9800",
            channel_chat_id=CHANNEL_CHAT.id,
            channel_message_ids=[77],
        )
        if status is not None:
            car.status = status
        await s.commit()
        car_id = car.id
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
                forward_origin=MessageOriginChannel(date=datetime.now(timezone.utc), chat=CHANNEL_CHAT, message_id=77),
                text="Gentra 2019 ...",
            ),
        ),
    )
    return car_id, auto_id


def _group_msg(
    uid: int, text_: str, *, thread: int | None, chat_id: int = GROUP_ID, reply_to: Message | None = None
) -> Update:
    return Update(
        update_id=next(_ids),
        message=Message(
            message_id=next(_ids),
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="supergroup"),
            from_user=User(id=uid, is_bot=False, first_name="Sardor"),
            text=text_,
            message_thread_id=thread,
            reply_to_message=reply_to,
        ),
    )


@pytest.fixture
def comment_ai(monkeypatch):
    from bot.ai import budget as budget_mod
    from bot.handlers import channel_comments

    channel_comments._ai_replies.clear()
    channel_comments._last_reply.clear()

    def install(fake):
        monkeypatch.setattr(channel_comments, "get_ai", lambda: fake)
        b = budget_mod.AIBudget(daily_usd=0, user_daily_limit=0)
        monkeypatch.setattr(channel_comments, "get_budget", lambda: b)
        return fake

    return install


async def test_comment_ai_answers_question_with_db_facts(env, comment_ai):
    import json as _json

    dp, bot, session, factory, _ = env
    fake = comment_ai(_FakeCommentAI([{"action": "reply", "category": "question", "reply": "Gentra hali sotuvda, narxi 9 800$."}]))
    car_id, thread = await _comment_setup(factory, dp, bot)

    await dp.feed_update(bot, _group_msg(9101, "narxi qancha? kraskasi bormi", thread=thread))
    sent = session.sent(SendMessage, GROUP_ID)
    assert sent[-1].text == "Gentra hali sotuvda, narxi 9 800$." and sent[-1].parse_mode is None
    assert sent[-1].reply_markup.inline_keyboard[0][0].url.endswith(f"start=car_{car_id}")
    payload = _json.loads(fake.calls[0][1])
    assert payload["shu_post_mashinasi"]["narx_usd"] == 9800
    assert "[Ovoz]" not in payload.get("post_matni", "")  # tasdiqlanmagan transkript ochiq javobga kirmaydi
    assert "xarid" not in fake.calls[0][1]  # xarid narxi/foyda kabi ichki maydonlar yo'q


async def test_comment_ai_negative_gets_polite_reply_and_admin_alert(env, comment_ai):
    dp, bot, session, factory, _ = env
    comment_ai(
        _FakeCommentAI(
            [{"action": "reply", "category": "negative", "reply": "Fikringiz uchun rahmat, menejerimiz bog'lanadi.", "admin_note": "narx qimmat deyapti"}]
        )
    )
    _, thread = await _comment_setup(factory, dp, bot)
    await dp.feed_update(bot, _group_msg(9102, "juda qimmat, aldov narx", thread=thread))
    assert "rahmat" in session.sent(SendMessage, GROUP_ID)[-1].text
    alert = session.sent(SendMessage, ADMIN_ID)[-1].text
    assert "salbiy fikr" in alert and "aldov narx" in alert and "narx qimmat deyapti" in alert


async def test_comment_ai_never_replies_to_toxic(env, comment_ai):
    dp, bot, session, factory, _ = env
    # Model adashib reply qaytarsa ham — haqoratga ochiq javob yo'q
    comment_ai(_FakeCommentAI([{"action": "reply", "category": "toxic", "reply": "..."}]))
    _, thread = await _comment_setup(factory, dp, bot)
    await dp.feed_update(bot, _group_msg(9103, "hammang firibgarsan", thread=thread))
    assert session.sent(SendMessage, GROUP_ID) == []
    assert "haqorat" in session.sent(SendMessage, ADMIN_ID)[-1].text


async def test_comment_ai_unverified_car_hides_numbers(env, comment_ai):
    import json as _json

    dp, bot, session, factory, _ = env
    fake = comment_ai(_FakeCommentAI([{"action": "ignore", "category": "chat"}]))
    _, thread = await _comment_setup(factory, dp, bot, status=CarStatus.REVIEW)
    await dp.feed_update(bot, _group_msg(9104, "probegi qancha", thread=thread))
    facts = _json.loads(fake.calls[0][1])["shu_post_mashinasi"]
    assert "narx_usd" not in facts and "probeg_km" not in facts and "tekshirilmoqda" in facts["holati"]
    assert session.sent(SendMessage, GROUP_ID) == []


async def test_comment_ai_skips_admins_emoji_and_foreign_groups(env, comment_ai):
    dp, bot, session, factory, _ = env
    fake = comment_ai(_FakeCommentAI([]))
    _, thread = await _comment_setup(factory, dp, bot)
    customer = _group_msg(9104, "narxi qancha?", thread=thread).message
    # menejer mijozga reply qilib javob yozdi — bot aralashmaydi
    await dp.feed_update(bot, _group_msg(ADMIN_ID, "narxi 9800, keling", thread=thread, reply_to=customer))
    await dp.feed_update(bot, _group_msg(9105, "👍👍", thread=thread))  # bo'sh — AI ga yuborilmaydi
    await dp.feed_update(bot, _group_msg(9106, "narxi qancha?", thread=None, chat_id=-100999))  # boshqa guruh
    assert fake.calls == [] and session.sent(SendMessage, GROUP_ID) == []


async def test_comment_ai_answers_admin_own_question_without_admin_alerts(env, comment_ai):
    from bot import config

    dp, bot, session, factory, _ = env
    fake = comment_ai(_FakeCommentAI([
        {"action": "reply", "category": "buy_intent", "reply": "Narxi 9 800$.", "buy_intent": True, "notify_admin": True},
    ]))
    _, thread = await _comment_setup(factory, dp, bot)
    await dp.feed_update(bot, _group_msg(ADMIN_ID, "narxi qancha, kreditga bormi?", thread=thread))  # admin sinab ko'rdi
    assert len(fake.calls) == 1
    assert session.sent(SendMessage, GROUP_ID)[-1].text == "Narxi 9 800$."
    assert session.sent(SendMessage, ADMIN_ID) == []  # o'z xabari haqida signal yo'q

    object.__setattr__(config.settings, "comments_answer_admins", False)
    try:
        await dp.feed_update(bot, _group_msg(ADMIN_ID, "probegi qancha?", thread=thread))
    finally:
        object.__setattr__(config.settings, "comments_answer_admins", True)
    assert len(fake.calls) == 1


async def test_comment_ai_post_text_used_when_car_unknown(env, comment_ai):
    import json as _json

    dp, bot, session, factory, _ = env
    fake = comment_ai(_FakeCommentAI([{"action": "reply", "category": "question", "reply": "Narxi 7 200$."}]))
    await _comment_setup(factory, dp, bot)
    old_post = Message(
        message_id=next(_ids),
        date=datetime.now(timezone.utc),
        chat=Chat(id=GROUP_ID, type="supergroup"),
        sender_chat=CHANNEL_CHAT,
        is_automatic_forward=True,
        forward_origin=MessageOriginChannel(date=datetime.now(timezone.utc), chat=CHANNEL_CHAT, message_id=5),
        text="Nexia 3 2017, narxi 7200$",
    )
    await dp.feed_update(bot, _group_msg(9110, "narxi qancha?", thread=None, reply_to=old_post))
    payload = _json.loads(fake.calls[0][1])
    assert "7200$" in payload["post_matni"] and "shu_post_mashinasi" not in payload


async def test_comment_ai_general_inventory_question(env, comment_ai):
    import json as _json

    dp, bot, session, factory, _ = env
    fake = comment_ai(_FakeCommentAI([{"action": "reply", "category": "question", "reply": "Gentra 2019 — 9 800$."}]))
    await _comment_setup(factory, dp, bot)
    await dp.feed_update(bot, _group_msg(9111, "qanaqa mashinalar bor sotuvda?", thread=None))
    payload = _json.loads(fake.calls[0][1])
    assert payload["hozir_sotuvdagi_mashinalardan"][0]["mashina"] == "Chevrolet Gentra 2019"


async def test_comment_ai_general_group_question_uses_inventory(env, comment_ai):
    import json as _json

    dp, bot, session, factory, _ = env
    fake = comment_ai(_FakeCommentAI([{"action": "reply", "category": "question", "reply": "Ha, Gentra 2019 bor."}]))
    await _comment_setup(factory, dp, bot)  # guruh endi «bizniki» deb taniladi
    await dp.feed_update(bot, _group_msg(9107, "gentra bormi sotuvda?", thread=None))
    payload = _json.loads(fake.calls[0][1])
    assert payload["sotuvdagi_mos_mashinalar"][0]["mashina"] == "Chevrolet Gentra 2019"
    assert session.sent(SendMessage, GROUP_ID)[-1].text == "Ha, Gentra 2019 bor."


async def test_comment_ai_rate_limited_per_user(env, comment_ai):
    dp, bot, session, factory, _ = env
    decisions = [{"action": "reply", "category": "question", "reply": f"javob {i}"} for i in range(5)]
    fake = comment_ai(_FakeCommentAI(decisions))
    _, thread = await _comment_setup(factory, dp, bot)
    for i in range(5):
        await dp.feed_update(bot, _group_msg(9108, f"savol {i}?", thread=thread))
    assert len(fake.calls) == 3  # 10 daqiqada ko'pi bilan 3 ta


async def test_comment_ai_error_falls_back_to_template(env, comment_ai):
    dp, bot, session, factory, _ = env
    comment_ai(_FakeCommentAI(fail=True))
    _, thread = await _comment_setup(factory, dp, bot)
    await dp.feed_update(bot, _group_msg(9109, "narxi qancha?", thread=thread))
    reply = session.sent(SendMessage, GROUP_ID)[-1].text
    assert "hali sotuvda" in reply and "$9 800" in reply


async def test_crafted_deep_links_do_not_crash(env):
    """Ochiq havola: start=car_<katta son> yoki lq_<katta son> — «topilmadi», texnik xato emas."""
    dp, bot, session, _, _ = env
    for payload in ("car_99999999999", "lq_99999999999", "car_²", "car_-1"):
        await dp.feed_update(bot, _text_update(CUSTOMER_ID, f"/start {payload}"))
    texts = [m.text or "" for m in session.sent(SendMessage, CUSTOMER_ID)]
    assert texts and not any("Texnik xato" in t for t in texts)
