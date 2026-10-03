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

from bot.config import settings
from bot.db.base import create_tables, dispose_engine, get_engine, get_session_factory, init_engine
from bot.db.migrate import (
    apply_car_indexes,
    apply_lead_business_columns,
    apply_listing_freeze_columns,
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
from bot.ai import close_ai, get_ai, get_budget
from bot.services.car_cards import notify_admins_text
from bot.instagram.client import get_ig
from bot.instagram.webhook import start_instagram_server
from bot.workers.sale_followup import sale_followup_loop
from bot.workers.lead_reminder import lead_reminder_loop
from bot.workers.listing_freeze import listing_freeze_loop
from bot.workers.stale_cars import stale_cars_loop

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
            await apply_car_indexes(get_engine())
            await apply_lead_business_columns(get_engine())
            await apply_listing_freeze_columns(get_engine())
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


async def _backfill_cars(bot: Bot) -> None:
    """Yangilanishdan oldin tasdiqlangan e'lonlar ham agent/katalog bazasida bo'lsin (bir martalik, xavfsiz)."""
    from bot.services.backfill import backfill_listing_cars

    channel_chat_id: int | None = None
    try:
        channel_chat_id = (await bot.get_chat(settings.channel_id)).id
    except Exception as e:
        logger.warning("Kanal ID sini aniqlab bo'lmadi (backfill kanal havolasisiz): %s", e)
    try:
        await backfill_listing_cars(get_session_factory(), channel_chat_id=channel_chat_id)
    except Exception:
        logger.exception("Eski e'lonlarni mashinalar bazasiga ko'chirib bo'lmadi")


async def _set_admin_commands(bot: Bot) -> None:
    """Adminlar uchun «/» menyusi (oddiy foydalanuvchilarga ko'rinmaydi)."""
    from aiogram.types import BotCommand, BotCommandScopeChat

    commands = [
        BotCommand(command="panel", description="🛠 Admin panel (CRM)"),
        BotCommand(command="leadlar", description="🔥 Ochiq mijozlar (leadlar)"),
        BotCommand(command="statistika", description="📊 Mashinalar statistikasi (30 kun)"),
        BotCommand(command="sotuvda", description="🟢 Sotuvdagi mashinalar"),
        BotCommand(command="tekshiruv", description="🟡 Tekshiruv kutayotgan postlar"),
        BotCommand(command="navbat", description="📝 Moderatsiya navbati (foydalanuvchi e'lonlari)"),
        BotCommand(command="umumiy", description="📈 Umumiy statistika (foydalanuvchi, e'lon, sotuv)"),
        BotCommand(command="mashina", description="🚗 Mashina kartasi: /mashina ID"),
        BotCommand(command="sotildi", description="🔴 Sotildi: /sotildi ID [narx]"),
        BotCommand(command="start", description="Asosiy menyu"),
    ]
    for aid in settings.admin_telegram_ids:
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=aid))
        except TelegramBadRequest as e:
            logger.warning("Admin %s uchun buyruqlar menyusi o'rnatilmadi: %s", aid, e)


async def _set_catalog_menu_button(bot: Bot) -> None:
    """Chat pastidagi Mini App tugmasi: hamma uchun «🚗 Katalog», adminlarga — «🛠 Admin panel» (CRM).

    Telegram menyu tugmasini har bir chat uchun alohida qo'yishga ruxsat beradi. Faqat HTTPS manzillar.
    """
    from aiogram.types import MenuButtonWebApp, WebAppInfo

    for name, url in (("CATALOG_URL", settings.catalog_url), ("CRM_URL", settings.crm_url)):
        if url and not url.startswith("https://"):
            logger.warning("%s https emas — Mini App tugmasi o'rnatilmadi: %s", name, url)
    if settings.catalog_url.startswith("https://"):
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🚗 Katalog", web_app=WebAppInfo(url=settings.catalog_url))
            )
        except TelegramBadRequest as e:
            logger.warning("Katalog menyu tugmasi o'rnatilmadi: %s", e)
    if settings.crm_url.startswith("https://"):
        for aid in settings.admin_telegram_ids:
            try:
                await bot.set_chat_menu_button(
                    chat_id=aid,
                    menu_button=MenuButtonWebApp(text="🛠 Admin panel", web_app=WebAppInfo(url=settings.crm_url)),
                )
            except TelegramBadRequest as e:
                # Admin hali botga /start bosmagan bo'lsa — chat yo'q
                logger.warning("Admin %s uchun panel tugmasi o'rnatilmadi: %s", aid, e)


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
    await _set_admin_commands(bot)
    await _backfill_cars(bot)
    get_ai()  # provayder/model logga yoziladi
    get_budget().set_alert(lambda text: notify_admins_text(bot, text))
    await _set_catalog_menu_button(bot)

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

    # Bot o'chiq paytda kelgan kanal postlari va mijoz xabarlari yo'qolmasin — Telegram ularni 24 soat saqlaydi
    await bot.delete_webhook(drop_pending_updates=False)

    # LEADERBOARD LOOP MUZLATILDI - Foydalanuvchi botdan chiqdi
    # worker_lb = asyncio.create_task(leaderboard_loop(bot, session_factory))
    worker_sale = asyncio.create_task(sale_followup_loop(bot, session_factory))
    heartbeat = asyncio.create_task(_heartbeat_loop(bot))
    worker_stale = asyncio.create_task(stale_cars_loop(bot, session_factory))
    worker_leads = asyncio.create_task(lead_reminder_loop(bot, session_factory))
    worker_freeze = asyncio.create_task(listing_freeze_loop(bot, session_factory))
    ig_runner = await start_instagram_server(bot, session_factory, get_ig())
    try:
        # channel_post / edited_channel_post ham kelishi uchun ishlatilayotgan update turlarini aniq so'raymiz
        await dp.start_polling(bot, handle_signals=True, allowed_updates=dp.resolve_used_update_types())
    finally:
        # worker_lb.cancel()
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        worker_sale.cancel()
        worker_stale.cancel()
        worker_leads.cancel()
        worker_freeze.cancel()
        # try:
        #     await worker_lb
        # except asyncio.CancelledError:
        #     pass
        for w in (worker_sale, worker_stale, worker_leads, worker_freeze):
            try:
                await w
            except asyncio.CancelledError:
                pass
        await close_ai()
        if ig_runner is not None:
            await ig_runner.cleanup()
        await get_ig().close()
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
