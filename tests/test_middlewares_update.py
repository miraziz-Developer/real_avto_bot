"""Middlewarelar `dp.update.middleware` sifatida (Update obyekti bilan) to'g'ri ishlashi."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.middlewares.database import DbSessionMiddleware
from bot.middlewares.errors import UnhandledErrorMiddleware
from bot.middlewares.rate_limit import _MAX_MSG_PER_MINUTE, RateLimitMiddleware, _bucket


@pytest.fixture(autouse=True)
def clear_bucket():
    _bucket.clear()
    yield
    _bucket.clear()


def _msg_update(uid: int = 42, chat_type: str = "private") -> Update:
    msg = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=uid if chat_type == "private" else -100500, type=chat_type),
        from_user=User(id=uid, is_bot=False, first_name="T"),
        text="salom",
    )
    return Update(update_id=1, message=msg)


def _cb_update(uid: int = 42) -> Update:
    cq = CallbackQuery(
        id="1",
        from_user=User(id=uid, is_bot=False, first_name="T"),
        chat_instance="x",
        data="home",
    )
    return Update(update_id=2, callback_query=cq)


async def test_rate_limit_blocks_update_wrapped_messages(monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    mw = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")

    for _ in range(_MAX_MSG_PER_MINUTE):
        assert await mw(handler, _msg_update(), {}) == "ok"
    assert handler.await_count == _MAX_MSG_PER_MINUTE

    # Limitdan oshgan xabarlar handlerga yetmaydi; ogohlantirish faqat bir marta.
    for _ in range(5):
        assert await mw(handler, _msg_update(), {}) is None
    assert handler.await_count == _MAX_MSG_PER_MINUTE
    assert answer.await_count == 1


async def test_rate_limit_other_user_not_affected(monkeypatch):
    monkeypatch.setattr(Message, "answer", AsyncMock())
    mw = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")
    for _ in range(_MAX_MSG_PER_MINUTE + 3):
        await mw(handler, _msg_update(uid=1), {})
    assert await mw(handler, _msg_update(uid=2), {}) == "ok"


async def test_rate_limit_ignores_group_messages():
    mw = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")
    for _ in range(_MAX_MSG_PER_MINUTE + 5):
        assert await mw(handler, _msg_update(chat_type="supergroup"), {}) == "ok"


async def test_rate_limit_callbacks_answered_silently(monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    mw = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")
    from bot.middlewares.rate_limit import _MAX_CB_PER_MINUTE

    for _ in range(_MAX_CB_PER_MINUTE + 3):
        await mw(handler, _cb_update(), {})
    assert handler.await_count == _MAX_CB_PER_MINUTE
    # 1 ta alert + 2 ta jim answer (spinner yopiladi)
    assert answer.await_count == 3


async def test_error_middleware_replies_for_update_and_db_rolls_back(monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    factory = lambda: session  # noqa: E731

    async def failing_handler(event, data):
        raise ValueError("boom")

    db_mw = DbSessionMiddleware(factory)
    err_mw = UnhandledErrorMiddleware()

    async def inner(event, data):
        return await db_mw(failing_handler, event, data)

    result = await err_mw(inner, _msg_update(), {})
    assert result is None
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    answer.assert_awaited_once()
