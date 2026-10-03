import asyncio
import logging
import os
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from bot.config import settings
from bot.db.base import create_tables, dispose_engine, get_engine, get_session_factory, init_engine
from bot.db.migrate import (
    apply_contest_tables,
    apply_listing_extra_details_column,
    apply_listing_location_column,
    apply_listing_payment_unique_id_column,
    apply_listing_payment_screenshot_column,
    apply_listing_price_ask_usd_rename,
    apply_listing_sale_followup_columns,
    apply_listing_seller_username_column,
    apply_listing_thread_tables,
    apply_performance_indexes,
    apply_user_leaderboard_alias_column,
    apply_wishlist_table,
)


from bot.handlers import register_handlers
from bot.middlewares.database import DbSessionMiddleware
from bot.middlewares.errors import UnhandledErrorMiddleware
from bot.middlewares.rate_limit import RateLimitMiddleware
from bot.middlewares.workflow import BotUsernameMiddleware
from bot.workers.sale_followup import sale_followup_loop

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    level = getattr(logging, settings.log_level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stdout,
        )
    else:
        for h in root.handlers:
            h.setLevel(level)


def _log_production_warnings() -> None:
    """Ko'p uchraydigan .env kamchiligi uchun ogohlantirish (bot ishga tushishni to'xtatmaydi)."""
    if not settings.admin_telegram_ids:
        logger.warning(
            "ADMIN_TELEGRAM_IDS bo'sh -- yangi e'lonlar moderatsiyaga tushmaydi. "
            ".env ga admin Telegram ID qo'shing."
        )
    if not (os.getenv("SALES_PHONE") or "").strip():
        logger.warning(
            "SALES_PHONE .env da ko'rsatilmagan -- kanalda standart test raqami chiqadi; prod uchun to'ldiring."
        )


async def _bootstrap_database() -> None:
    last_exc: BaseException | None = None
    for attempt in range(1, 4):
        try:
            init_engine(
                settings.database_url,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_recycle=settings.db_pool_recycle,
                pool_timeout=settings.db_pool_timeout,
            )
            await create_tables()
            await apply_user_leaderboard_alias_column(get_engine())
            await apply_listing_extra_details_column(get_engine())
            await apply_listing_seller_username_column(get_engine())
            await apply_listing_price_ask_usd_rename(get_engine())
            await apply_wishlist_table(get_engine())
            await apply_contest_tables(get_engine())
            await apply_listing_thread_tables(get_engine())
            await apply_listing_sale_followup_columns(get_engine())
            await apply_performance_indexes(get_engine())
            await apply_listing_payment_screenshot_column(get_engine())
            await apply_listing_location_column(get_engine())
            await apply_listing_payment_unique_id_column(get_engine())
            return
        except BaseException as e:
            last_exc = e
            logger.warning("Baza tayyorlash %s/3 muvaffaqiyatsiz: %s", attempt, e)
            try:
                await dispose_engine()
            except Exception:
                logger.exception("dispose_engine (retry oldidan)")
            await asyncio.sleep(min(30, 2**attempt))
    assert last_exc is not None
    raise last_exc


async def _fsm_storage():
    if not settings.redis_url:
        logger.info("REDIS_URL yo'q -- FSM xotirasi jarayon ichida (restartda FSM yo'qoladi).")
        return MemoryStorage()
    try:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage.from_url(settings.redis_url)
        import redis.asyncio as redis_mod

        r = redis_mod.from_url(settings.redis_url, decode_responses=False)
        try:
            await asyncio.wait_for(r.ping(), timeout=5.0)
        finally:
            await r.aclose()
        logger.info("FSM saqlash: Redis")
        return storage
    except Exception:
        logger.exception("Redis ga ulanib bo'lmadi -- FSM uchun xotira ishlatiladi.")
        return MemoryStorage()


async def _verify_bot_chat(bot: Bot, *, env_var: str, chat_ref: str, purpose: str) -> None:
    """Kanal/superguruh -- bot a'zo/admin bo'lishi kerak."""
    try:
        chat = await bot.get_chat(chat_ref)
        logger.info(
            "%s OK: %s (chat_id=%s)",
            purpose,
            getattr(chat, "title", None) or getattr(chat, "username", None) or chat_ref,
            chat.id,
        )
    except TelegramBadRequest as e:
        logger.error(
            "%s: bot ushbu chatni topa olmadi yoki a'zo emas: %s=%r. Xato: %s. "
            ".env da -100... yoki @username; botni kanalga admin qilib qo'shing.",
            purpose,
            env_var,
            chat_ref,
            e,
        )


async def _verify_listings_post_channel(bot: Bot) -> None:
    """E'lon postlari ketadigan kanal (CHANNEL_ID bilan bir xil)."""
    await _verify_bot_chat(bot, env_var="CHANNEL_ID", chat_ref=settings.channel_id, purpose="E'lon kanali")


async def _verify_leaderboard_channel(bot: Bot) -> None:
    await _verify_bot_chat(
        bot,
        env_var="LEADERBOARD_CHANNEL_ID",
        chat_ref=settings.leaderboard_channel_id,
        purpose="TOP/reyting kanali",
    )


async def _verify_reviews_channel(bot: Bot) -> None:
    await _verify_bot_chat(
        bot,
        env_var="REVIEWS_CHANNEL_ID",
        chat_ref=settings.reviews_channel_id,
        purpose="Sharhlar kanali",
    )


HEARTBEAT_FILE = os.getenv("BOT_HEARTBEAT_FILE", "/tmp/bot_heartbeat")
HEARTBEAT_EVERY_SECONDS = 30


async def _heartbeat_loop(bot: Bot) -> None:
    """Docker healthcheck uchun: Telegram API ga ulanish ishlasa, faylga vaqt yoziladi."""
    while True:
        try:
            await asyncio.wait_for(bot.get_me(), timeout=20)
            with open(HEARTBEAT_FILE, "w", encoding="ascii") as f:
                f.write(str(int(time.time())))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Heartbeat: Telegram API javob bermadi: %s", e)
        await asyncio.sleep(HEARTBEAT_EVERY_SECONDS)


async def _set_admin_commands(bot: Bot) -> None:
    """Admin chatlarida /stats va /pending menyuda ko'rinsin (oddiy foydalanuvchilarda emas)."""
    commands = [
        BotCommand(command="start", description="Bosh menyu"),
        BotCommand(command="pending", description="Moderatsiya navbati"),
        BotCommand(command="stats", description="Statistika"),
    ]
    for aid in settings.admin_telegram_ids:
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=aid))
        except TelegramBadRequest as e:
            logger.warning("Admin %s uchun buyruqlar menyusi o'rnatilmadi: %s", aid, e)


async def _run() -> None:
    _configure_logging()

    _log_production_warnings()

    await _bootstrap_database()

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await _verify_listings_post_channel(bot)
    # LEADERBOARD MUZLATILDI - Foydalanuvchi botdan chiqdi
    # await _verify_leaderboard_channel(bot)
    await _verify_reviews_channel(bot)

    storage = await _fsm_storage()
    dp = Dispatcher(storage=storage)
    session_factory = get_session_factory()

    # Tartib muhim (birinchi = eng tashqi):
    #   xato ushlash → flood cheklovi → bot username → DB sessiya → handler.
    # Xato DB sessiyasidan o'tib (rollback) keyin ushlanadi; cheklangan update DB ga tegmaydi.
    dp.update.middleware(UnhandledErrorMiddleware())
    dp.update.middleware(RateLimitMiddleware())
    dp.update.middleware(BotUsernameMiddleware())
    dp.update.middleware(DbSessionMiddleware(session_factory))
    register_handlers(dp)

    # Restart paytida yozilgan xabarlar yo‘qolmasin (flood’dan rate limit himoya qiladi).
    await bot.delete_webhook(drop_pending_updates=False)

    # LEADERBOARD LOOP MUZLATILDI - Foydalanuvchi botdan chiqdi
    # worker_lb = asyncio.create_task(leaderboard_loop(bot, session_factory))
    await _set_admin_commands(bot)
    worker_sale = asyncio.create_task(sale_followup_loop(bot, session_factory))
    heartbeat = asyncio.create_task(_heartbeat_loop(bot))
    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        # worker_lb.cancel()
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        worker_sale.cancel()
        # try:
        #     await worker_lb
        # except asyncio.CancelledError:
        #     pass
        try:
            await worker_sale
        except asyncio.CancelledError:
            pass
        try:
            await storage.close()
        except Exception:
            logger.exception("FSM storage yopish")
        await bot.session.close()
        await dispose_engine()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
