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
