"""Tasdiqlangan e'lonlar uchun har N soatda «sotildi / sotilmadi» so‘rovi (DM)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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


def _loop_sleep_seconds(interval: timedelta) -> int:
    """Interval qisqa bo‘lsa worker tez-tez uyg‘onadi (test uchun)."""
    total = int(interval.total_seconds())
    if total <= 0:
        return 15
    return min(300, max(10, total // 4))


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
            now = datetime.now(timezone.utc)
            async with session_factory() as session:
                crm = CrmRepository(session)
                rows = await crm.listings_due_for_sale_followup(
                    now=now,
                    interval=interval,
                    first_after=first_after,
                    limit=30,
                )
                await session.commit()

            for sub in rows:
                text = sale_followup_prompt_html(sub, repeat_label=repeat)
                async with session_factory() as session:
                    try:
                        await bot.send_message(
                            sub.user_telegram_id,
                            text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=sale_followup_prompt_markup(sub.id),
                        )
                        crm = CrmRepository(session)
                        await crm.mark_sale_prompt_sent(sub.id)
                        await session.commit()
                    except (TelegramBadRequest, TelegramForbiddenError) as e:
                        await session.rollback()
                        logger.warning(
                            "Sotilish so'rovi yuborilmadi (e'lon #%s, user %s): %s",
                            sub.id,
                            sub.user_telegram_id,
                            e,
                        )
                    except Exception:
                        await session.rollback()
                        logger.exception("Sotilish so'rovi worker: e'lon #%s", sub.id)

            await asyncio.sleep(poll_sleep)
    except asyncio.CancelledError:
        logger.info("Sotilish so'rovi worker to'xtatildi")
