import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings
from bot.db.base import create_tables, dispose_engine, get_engine, get_session_factory, init_engine
from bot.db.migrate import apply_user_leaderboard_alias_column
from bot.handlers import register_handlers
from bot.middlewares.database import DbSessionMiddleware
from bot.middlewares.workflow import BotUsernameMiddleware
from bot.workers.leaderboard import leaderboard_loop


async def _run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    init_engine(settings.database_url)
    await create_tables()
    await apply_user_leaderboard_alias_column(get_engine())

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    session_factory = get_session_factory()

    dp.update.middleware(BotUsernameMiddleware())
    dp.update.middleware(DbSessionMiddleware(session_factory))

    register_handlers(dp)

    await bot.delete_webhook(drop_pending_updates=True)

    worker = asyncio.create_task(leaderboard_loop(bot, session_factory))
    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        await dispose_engine()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
