import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from bot.config import settings
from bot.db.repositories import AppMetaRepository, UserRepository
from bot.services.leaderboard import LeaderboardService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def leaderboard_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    try:
        while True:
            await asyncio.sleep(60)
            async with session_factory() as session:
                try:
                    meta = AppMetaRepository(session)
                    users_repo = UserRepository(session)
                    now = datetime.now(timezone.utc)
                    next_at = await meta.get_next_leaderboard_at()
                    if next_at is None:
                        await meta.set_next_leaderboard_at(
                            now + timedelta(days=settings.leaderboard_interval_days)
                        )
                        await session.commit()
                        continue

                    if now < next_at:
                        await session.commit()
                        continue

                    rows = await users_repo.top_referrers(15)
                    await LeaderboardService.post_to_channel(bot, rows)
                    await meta.set_next_leaderboard_at(
                        now + timedelta(days=settings.leaderboard_interval_days)
                    )
                    await session.commit()
                    logger.info("TOP-15 post kanalga yuborildi")
                except Exception:
                    await session.rollback()
                    logger.exception("Leaderboard worker xatosi")
    except asyncio.CancelledError:
        logger.info("Leaderboard worker to‘xtatildi")
