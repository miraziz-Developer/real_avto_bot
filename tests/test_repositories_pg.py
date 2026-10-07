"""Haqiqiy PostgreSQL ustida parallel holatlar (qulflar) testi.

Ishga tushirish: TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5432/ra_test pytest
O'zgaruvchi bo'lmasa testlar o'tkazib yuboriladi.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.migrate import apply_listing_payment_unique_id_column
from bot.db.models import Base, ListingSubmissionStatus
from bot.db.repositories import CrmRepository

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL berilmagan")


@pytest.fixture
async def factory():
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await apply_listing_payment_unique_id_column(engine)  # idempotent bo'lishi kerak
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _new_listing(factory, *, tg_id: int = 777, photos=("p1", "p2", "p3"), pay_uid: str | None = None) -> int:
    async with factory() as s:
        crm = CrmRepository(s)
        client = await crm.get_or_create_client(telegram_id=tg_id, full_name="Test", phone="+998901234567")
        row = await crm.create_listing_submission(
            client_id=client.id,
            user_telegram_id=tg_id,
            brand="Chevrolet",
            model="Cobalt",
            year=2020,
            mileage=50000,
            condition_key="yaxshi",
            has_accident=False,
            price_ask_usd=10000,
            paint_status="toza",
            location="Toshkent",
            phone="+998901234567",
            photo_file_ids=list(photos),
            payment_screenshot_file_id="pay",
            payment_screenshot_unique_id=pay_uid,
        )
        await s.commit()
        return row.id


async def test_two_admins_cannot_both_approve(factory):
    lid = await _new_listing(factory)
    first_locked = asyncio.Event()

    async def admin_a():
        async with factory() as s:
            crm = CrmRepository(s)
            sub = await crm.lock_pending_listing(lid)
            assert sub is not None
            first_locked.set()
            await asyncio.sleep(0.3)  # kanalga post qilish vaqti
            updated = await crm.try_mark_listing_approved(lid, channel_message_id=1)
            await s.commit()
            return updated is not None

    async def admin_b():
        await first_locked.wait()
        async with factory() as s:
            crm = CrmRepository(s)
            sub = await crm.lock_pending_listing(lid)  # A commit qilguncha kutadi
            await s.commit()
            return sub is not None

    a, b = await asyncio.gather(admin_a(), admin_b())
    assert a is True
    assert b is False

    async with factory() as s:
        sub = await CrmRepository(s).get_listing_submission(lid)
        assert sub.status == ListingSubmissionStatus.APPROVED


async def test_reject_after_approve_is_refused(factory):
    lid = await _new_listing(factory)
    async with factory() as s:
        crm = CrmRepository(s)
        assert await crm.lock_pending_listing(lid) is not None
        await crm.try_mark_listing_approved(lid, channel_message_id=1)
        await s.commit()
    async with factory() as s:
        assert await CrmRepository(s).try_mark_listing_rejected(lid, reason="kech") is None


async def test_double_submit_is_detected_under_user_lock(factory):
    photos = ["a", "b", "c"]
    holding = asyncio.Event()

    async def submit(wait_for: asyncio.Event | None):
        if wait_for is not None:
            await wait_for.wait()
        async with factory() as s:
            crm = CrmRepository(s)
            await crm.acquire_user_submit_lock(555)
            if wait_for is None:
                holding.set()
                await asyncio.sleep(0.3)
            dup = await crm.find_recent_duplicate_listing(user_telegram_id=555, photo_file_ids=photos)
            if dup is not None:
                await s.commit()
                return "duplicate"
            client = await crm.get_or_create_client(telegram_id=555, full_name=None, phone="1")
            await crm.create_listing_submission(
                client_id=client.id,
                user_telegram_id=555,
                brand="Kia",
                model="K5",
                year=2022,
                mileage=1,
                condition_key="ideal",
                has_accident=False,
                price_ask_usd=1,
                paint_status="toza",
                phone="1",
                photo_file_ids=photos,
            )
            await s.commit()
            return "created"

    results = await asyncio.gather(submit(None), submit(holding))
    assert sorted(results) == ["created", "duplicate"]


async def test_different_photos_are_not_duplicates(factory):
    await _new_listing(factory, tg_id=1, photos=("x", "y", "z"))
    async with factory() as s:
        dup = await CrmRepository(s).find_recent_duplicate_listing(
            user_telegram_id=1, photo_file_ids=["x", "y", "other"]
        )
        assert dup is None


async def test_payment_screenshot_reuse_found(factory):
    first = await _new_listing(factory, tg_id=1, pay_uid="UNIQ")
    second = await _new_listing(factory, tg_id=2, photos=("q", "w", "e"), pay_uid="UNIQ")
    await _new_listing(factory, tg_id=3, photos=("r", "t", "y"), pay_uid="OTHER")
    async with factory() as s:
        reused = await CrmRepository(s).listings_with_payment_screenshot("UNIQ", exclude_id=second)
        assert [r.id for r in reused] == [first]


async def test_sale_feedback_double_click_only_once(factory):
    lid = await _new_listing(factory, tg_id=9)
    async with factory() as s:
        crm = CrmRepository(s)
        await crm.lock_pending_listing(lid)
        await crm.try_mark_listing_approved(lid, channel_message_id=1)
        await s.commit()

    async def click():
        async with factory() as s:
            r = await CrmRepository(s).try_set_sale_feedback_pending(lid, user_telegram_id=9)
            await asyncio.sleep(0.1)
            await s.commit()
            return r is not None

    results = await asyncio.gather(click(), click())
    assert sorted(results) == [False, True]


async def test_stale_feedback_pending_is_prompted_again(factory):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    lid = await _new_listing(factory, tg_id=11)
    async with factory() as s:
        crm = CrmRepository(s)
        await crm.lock_pending_listing(lid)
        await crm.try_mark_listing_approved(lid, channel_message_id=1)
        await s.commit()
    async with factory() as s:
        assert await CrmRepository(s).try_set_sale_feedback_pending(lid, user_telegram_id=11) is not None
        await s.commit()
    # Foydalanuvchi sharh yozmay 2 kun o'tdi.
    async with factory() as s:
        await s.execute(
            text("update listing_submissions set updated_at = now() - interval '2 days' where id=:i"), {"i": lid}
        )
        await s.commit()

    now = datetime.now(timezone.utc)
    async with factory() as s:
        due = await CrmRepository(s).listings_due_for_sale_followup(
            now=now, interval=timedelta(hours=24), first_after=timedelta(hours=1)
        )
        assert [d.id for d in due] == [lid]
        await CrmRepository(s).mark_sale_prompt_sent(lid)
        await s.commit()
    async with factory() as s:
        crm = CrmRepository(s)
        sub = await crm.get_listing_submission(lid)
        assert sub.sale_status == "open"
        # Yangi so'rovdagi «Sotildi» tugmasi yana ishlaydi.
        assert await crm.try_set_sale_feedback_pending(lid, user_telegram_id=11) is not None


async def test_fresh_feedback_pending_is_not_interrupted(factory):
    from datetime import datetime, timedelta, timezone

    lid = await _new_listing(factory, tg_id=12)
    async with factory() as s:
        crm = CrmRepository(s)
        await crm.lock_pending_listing(lid)
        await crm.try_mark_listing_approved(lid, channel_message_id=1)
        await s.commit()
    async with factory() as s:
        await CrmRepository(s).try_set_sale_feedback_pending(lid, user_telegram_id=12)
        await s.commit()
    async with factory() as s:
        due = await CrmRepository(s).listings_due_for_sale_followup(
            now=datetime.now(timezone.utc) + timedelta(hours=2),
            interval=timedelta(hours=24),
            first_after=timedelta(hours=1),
        )
        assert due == []


async def test_admin_stats_and_pending_queue(factory):
    a = await _new_listing(factory, tg_id=21)
    b = await _new_listing(factory, tg_id=22, photos=("1", "2", "3"))
    async with factory() as s:
        crm = CrmRepository(s)
        await crm.lock_pending_listing(a)
        await crm.try_mark_listing_approved(a, channel_message_id=5)
        await s.commit()
    async with factory() as s:
        crm = CrmRepository(s)
        st = await crm.admin_stats()
        assert st["pending"] == 1
        assert st["approved"] == 1
        assert st["approved_24h"] == 1
        assert st["submitted_24h"] == 2
        assert st["clients"] == 2
        assert [r.id for r in await crm.list_pending_listings()] == [b]


async def test_uzs_to_usd_migrations_run_only_once(factory):
    """Eski xato: har restartda 1 mln dan katta byudjet/narx qayta-qayta kursga bo'linardi."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    from bot.db.migrate import apply_listing_price_ask_usd_rename, apply_wishlist_table

    engine = create_async_engine(DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(sql_text("DELETE FROM app_meta WHERE key LIKE 'migr:%'"))
        lid = await _new_listing(factory, tg_id=31)
        async with factory() as s:
            await s.execute(sql_text("update listing_submissions set price_ask_usd = 130000000 where id=:i"), {"i": lid})
            await s.commit()
        # 1-ishga tushish: eski so'mdagi qiymat USD ga o'tadi
        await apply_listing_price_ask_usd_rename(engine)
        await apply_wishlist_table(engine)
        async with factory() as s:
            from bot.db.migrate import _usd_rate

            assert (await CrmRepository(s).get_listing_submission(lid)).price_ask_usd == 130000000 // _usd_rate()
            await s.execute(sql_text("update listing_submissions set price_ask_usd = 2000000 where id=:i"), {"i": lid})
            await s.commit()
        # Keyingi restartlar — tegmaydi
        await apply_listing_price_ask_usd_rename(engine)
        await apply_listing_price_ask_usd_rename(engine)
        async with factory() as s:
            assert (await CrmRepository(s).get_listing_submission(lid)).price_ask_usd == 2000000
    finally:
        await engine.dispose()


async def test_parallel_messages_open_one_lead_and_one_admin_takes_it(factory):
    from bot.db.leads_repo import LeadRepository

    async def open_lead():
        async with factory() as s:
            lead, created = await LeadRepository(s).get_or_create_open(4242, name="Aziz")
            await asyncio.sleep(0.2)  # agent javobi tayyorlanayotgan vaqt
            await s.commit()
            return lead.id, created

    results = await asyncio.gather(open_lead(), open_lead())
    assert results[0][0] == results[1][0]
    assert sorted(c for _, c in results) == [False, True]

    lead_id = results[0][0]

    async def take(admin_id: int):
        async with factory() as s:
            repo = LeadRepository(s)
            lead = await repo.get(lead_id)
            ok = await repo.take(lead, admin_id)
            await asyncio.sleep(0.2)
            await s.commit()
            return ok

    outcomes = await asyncio.gather(take(1), take(2))
    assert sorted(outcomes) == [False, True]


async def test_backfill_old_approved_listings_into_cars(factory):
    from sqlalchemy import select as sa_select

    from bot.db.models import Car
    from bot.db.cars_repo import CarRepository
    from bot.services.backfill import backfill_listing_cars

    on_sale = await _new_listing(factory, tg_id=51)
    sold = await _new_listing(factory, tg_id=52, photos=("s1", "s2", "s3"))
    pending = await _new_listing(factory, tg_id=53, photos=("q1", "q2", "q3"))
    async with factory() as s:
        crm = CrmRepository(s)
        for lid in (on_sale, sold):
            await crm.lock_pending_listing(lid)
            await crm.try_mark_listing_approved(lid, channel_message_id=900 + lid)
        (await crm.get_listing_submission(sold)).sale_status = "sold"
        await s.commit()

    assert await backfill_listing_cars(factory, channel_chat_id=-100777) == 1
    assert await backfill_listing_cars(factory, channel_chat_id=-100777) == 0  # takroriy ishga tushish — dublikat yo'q
    async with factory() as s:
        cars = (await s.execute(sa_select(Car))).scalars().all()
        assert [c.listing_submission_id for c in cars] == [on_sale]
        car = cars[0]
        assert car.status == "active" and car.channel_chat_id == -100777 and car.channel_message_ids == [900 + on_sale]
        # Kanal eksporti importi yoki «sotildi» reply shu postni topadi
        assert (await CarRepository(s).find_by_channel_message(-100777, 900 + on_sale)).id == car.id
    assert pending  # tasdiqlanmagan e'lon ko'chirilmaydi
