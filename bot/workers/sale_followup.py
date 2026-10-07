"""Tasdiqlangan e'lonlar uchun har N soatda «sotildi / sotilmadi» so‘rovi (DM)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, UTC

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import (
    sale_followup_first_prompt_after,
    sale_followup_interval_timedelta,
    sale_followup_repeat_label,
    settings,
)
from bot.db.repositories import CrmRepository
from bot.services.sale_followup_prompt import sale_followup_prompt_html, sale_followup_prompt_markup

logger = logging.getLogger(__name__)


# Telegram: bitta botdan ~30 xabar/soniya; ehtiyot uchun sekinroq.
_SEND_PAUSE_SECONDS = 0.1


def _loop_sleep_seconds(interval: timedelta) -> int:
    """Interval qisqa bo‘lsa worker tez-tez uyg‘onadi (test uchun)."""
    total = int(interval.total_seconds())
    if total <= 0:
        return 15
    return min(300, max(10, total // 4))


async def _send_prompt(bot: Bot, session_factory: async_sessionmaker[AsyncSession], sub, repeat: str) -> None:
    """Bitta e'lon egasiga so'rov. Yuborib bo'lmasa ham vaqt belgilanadi — aks holda
    botni bloklagan foydalanuvchiga har bir tsiklda qayta-qayta urinib yotardik."""
    text = sale_followup_prompt_html(sub, repeat_label=repeat)
    try:
        await bot.send_message(
            sub.user_telegram_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=sale_followup_prompt_markup(sub.id),
        )
    except TelegramRetryAfter as e:
        # Flood limit: shu e'lonni keyingi tsiklga qoldiramiz.
        logger.warning("Sotilish so'rovi: Telegram flood limit, %ss kutiladi", e.retry_after)
        await asyncio.sleep(e.retry_after)
        return
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning(
            "Sotilish so'rovi yuborilmadi (e'lon #%s, user %s): %s",
            sub.id,
            sub.user_telegram_id,
            e,
        )
    async with session_factory() as session:
        await CrmRepository(session).mark_sale_prompt_sent(sub.id)
        await session.commit()


async def _run_once(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval: timedelta,
    first_after: timedelta,
    repeat: str,
) -> int:
    now = datetime.now(UTC)
    async with session_factory() as session:
        rows = await CrmRepository(session).listings_due_for_sale_followup(
            now=now,
            interval=interval,
            first_after=first_after,
            limit=30,
        )
        await session.commit()

    for sub in rows:
        try:
            await _send_prompt(bot, session_factory, sub, repeat)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sotilish so'rovi worker: e'lon #%s", sub.id)
        await asyncio.sleep(_SEND_PAUSE_SECONDS)
    return len(rows)


async def sale_followup_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    interval = sale_followup_interval_timedelta(settings)
    first_after = sale_followup_first_prompt_after(interval, settings)
    repeat = sale_followup_repeat_label(settings)
    poll_sleep = _loop_sleep_seconds(interval)
    logger.info(
        "Sotilish so‘rovi worker: takror=%s, birinchi_DM=%s, uyqu=%ss",
        interval,
        first_after,
        poll_sleep,
    )
    try:
        while True:
            try:
                await _run_once(
                    bot,
                    session_factory,
                    interval=interval,
                    first_after=first_after,
                    repeat=repeat,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Baza vaqtincha ishlamasa ham worker to'xtab qolmasin.
                logger.exception("Sotilish so'rovi worker tsikli xatosi — keyingi tsiklda qayta urinish")
            await asyncio.sleep(poll_sleep)
    except asyncio.CancelledError:
        logger.info("Sotilish so'rovi worker to'xtatildi")
        raise
