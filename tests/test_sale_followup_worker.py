"""Sotilish so'rovi worker: bloklangan foydalanuvchiga qayta-qayta urinmaslik, xatoda to'xtamaslik."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from bot.workers import sale_followup as sf


def _sub(i: int = 1):
    return SimpleNamespace(
        id=i, user_telegram_id=100 + i, brand="Chevrolet", model="Cobalt", year=2020, price_ask_usd=10000
    )


class _FakeSession:
    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


@pytest.fixture
def repo(monkeypatch):
    inst = MagicMock()
    inst.mark_sale_prompt_sent = AsyncMock()
    inst.listings_due_for_sale_followup = AsyncMock(return_value=[_sub(1), _sub(2)])
    monkeypatch.setattr(sf, "CrmRepository", lambda session: inst)
    monkeypatch.setattr(sf, "sale_followup_prompt_html", lambda sub, repeat_label: "matn")
    monkeypatch.setattr(sf, "sale_followup_prompt_markup", lambda lid: None)
    monkeypatch.setattr(sf, "_SEND_PAUSE_SECONDS", 0)
    return inst


async def test_blocked_user_still_marked_as_prompted(repo):
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="blocked")
    )
    await sf._send_prompt(bot, _FakeSession, _sub(), "har 24 soatda")
    repo.mark_sale_prompt_sent.assert_awaited_once_with(1)


async def test_unexpected_error_does_not_stop_batch(repo):
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[RuntimeError("tarmoq"), None])
    n = await sf._run_once(
        bot, _FakeSession, interval=timedelta(hours=24), first_after=timedelta(hours=1), repeat="x"
    )
    assert n == 2
    assert bot.send_message.await_count == 2
    # Birinchisi xato berdi (belgilanmaydi, keyingi tsiklda qayta urinadi), ikkinchisi belgilandi.
    repo.mark_sale_prompt_sent.assert_awaited_once_with(2)
