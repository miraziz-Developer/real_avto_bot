"""Instagram postlari → bazadagi mashinalar (IG_ACCESS_TOKEN bo'lsa).

E'lonlar avval Telegram kanalga chiqadi va bazaga tushadi; keyin o'sha mashinaning reklama videosi Instagram'ga
joylanadi. Worker har IG_SYNC_MINUTES daqiqada oxirgi postlarni ko'radi va har yangi postni mashinaga bog'laydi:
  1. post matni (kod, model/yil/probeg/narx);
  2. matn yetmasa — videoni AI ko'radi va eshitadi (model, rang, yil, aytilgan narx) va bazadan yagona mosini topadi;
  3. topilmasa — adminlardan tugmalar bilan so'raydi (yangi postlar uchun).
Shunda komment kelganda mashina allaqachon ma'lum bo'ladi. Natija adminlarga xabar qilinadi (noto'g'ri bo'lsa /ig).
Token (60 kunlik) haftada bir yangilanadi va bazada (app_meta) saqlanadi.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.ai import get_ai, get_budget
from bot.ai.errors import AIError
from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import AppMeta, Car
from bot.instagram.car_match import get_post, match_caption, match_parsed, remember_post
from bot.instagram.client import IGError, InstagramClient
from bot.instagram.manychat import ADMIN_CAR_CHOICES, ask_admins_about_post
from bot.services.car_cards import notify_admins_text
from bot.services.car_extract import extract_car

logger = logging.getLogger(__name__)

RECENT_MEDIA = 15
VIDEOS_PER_RUN = 5
VIDEO_MAX_BYTES = 19 * 1024 * 1024
# Shundan eski postlar haqida adminlardan so'ralmaydi (birinchi ishga tushishda eski postlar ko'p bo'ladi)
ASK_ADMIN_MAX_AGE = timedelta(days=14)
TOKEN_META_KEY = "ig_access_token"
TOKEN_REFRESH_EVERY = timedelta(days=7)
VIDEO_TYPES = frozenset({"VIDEO", "REELS"})


def _posted_at(media: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(media.get("timestamp", "")).replace("+0000", "+00:00"))
    except ValueError:
        return None


async def _watch_video(ig: InstagramClient, url: str) -> str:
    ai = get_ai()
    if not (ai.enabled and getattr(ai, "supports_video", False)) or not await get_budget().allow_user("ig:sync"):
        return ""
    try:
        data, mime = await ig.download(url, max_bytes=VIDEO_MAX_BYTES)
        if not data:
            return ""
        return await ai.transcribe(data, filename="video.mp4", mime_type=mime if mime.startswith("video/") else "video/mp4")
    except (AIError, TimeoutError, OSError) as e:
        logger.warning("Instagram videosi tahlil qilinmadi: %s", e)
        return ""


async def sync_once(bot: Bot, session_factory: async_sessionmaker[AsyncSession], ig: InstagramClient) -> int:
    """Yangi postlarni bog'laydi. Qaytaradi: shu safar bog'langan postlar soni."""
    linked = 0
    videos_left = VIDEOS_PER_RUN
    now = datetime.now(UTC)
    for media in await ig.list_media(RECENT_MEDIA):
        ref = str(media["id"])
        caption = media.get("caption") or ""
        async with session_factory() as session:
            cars = CarRepository(session)
            post = await get_post(cars, ref)
            if post is not None and (post.car_id or post.source == "admin" or post.asked_admin_at):
                continue
            car: Car | None = await match_caption(cars, caption)
            how = "post matni"
            stored = caption
            if car is None and media.get("media_type") in VIDEO_TYPES and media.get("media_url") and videos_left > 0:
                videos_left -= 1
                seen = await _watch_video(ig, media["media_url"])
                if seen:
                    stored = f"{caption}\n[Video]: {seen}".strip()
                    parsed = await extract_car(stored, ai=get_ai(), usd_rate_uzs=settings.usd_rate_uzs)
                    car = await match_parsed(cars, parsed)
                    how = "video"
            post = await remember_post(cars, ref, car=car, caption=stored or None, permalink=media.get("permalink"))
            posted = _posted_at(media)
            ask = car is None and (posted is None or now - posted <= ASK_ADMIN_MAX_AGE)
            choices: list[Car] = []
            if ask:
                post.asked_admin_at = now
                choices = await cars.search_offerable(limit=ADMIN_CAR_CHOICES)
            await session.commit()
        if car is not None:
            linked += 1
            link = f'<a href="{html.escape(post.permalink)}">Instagram post</a>' if post.permalink else "Instagram post"
            await notify_admins_text(
                bot,
                f"📸 {link} → #{car.id} {html.escape(car.title)} ({how} bo'yicha).\n"
                f"Noto'g'ri bo'lsa: <code>/ig {html.escape(ref)} ID</code>",
            )
        elif ask:
            await ask_admins_about_post(bot, post, choices)
    return linked


async def _load_token(session_factory: async_sessionmaker[AsyncSession], ig: InstagramClient) -> datetime | None:
    """Bazada yangilangan token bo'lsa — o'shani ishlatamiz (.env dagisi eskirgan bo'lishi mumkin)."""
    async with session_factory() as session:
        row = await session.get(AppMeta, TOKEN_META_KEY)
    if row is None or "|" not in row.value:
        return None
    saved_at, token = row.value.split("|", 1)
    ig.set_token(token)
    try:
        return datetime.fromisoformat(saved_at)
    except ValueError:
        return None


async def _refresh_token(session_factory: async_sessionmaker[AsyncSession], ig: InstagramClient) -> datetime | None:
    try:
        token = await ig.refresh_token()
    except (IGError, TimeoutError, OSError) as e:
        logger.warning("Instagram token yangilanmadi: %s", e)
        return None
    if not token:
        return None
    now = datetime.now(UTC)
    async with session_factory() as session:
        row = await session.get(AppMeta, TOKEN_META_KEY)
        value = f"{now.isoformat()}|{token}"
        if row is None:
            session.add(AppMeta(key=TOKEN_META_KEY, value=value))
        else:
            row.value = value
        await session.commit()
    logger.info("Instagram token yangilandi")
    return now


async def ig_sync_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession], ig: InstagramClient) -> None:
    interval = max(2, settings.ig_sync_minutes) * 60
    refreshed = await _load_token(session_factory, ig)
    logger.info("Instagram postlari kuzatuvi: har %s daqiqada", interval // 60)
    while True:
        try:
            if refreshed is None or datetime.now(UTC) - refreshed > TOKEN_REFRESH_EVERY:
                refreshed = await _refresh_token(session_factory, ig) or datetime.now(UTC) - TOKEN_REFRESH_EVERY / 2
            n = await sync_once(bot, session_factory, ig)
            if n:
                logger.info("Instagram: %s ta post mashinaga bog'landi", n)
        except asyncio.CancelledError:
            raise
        except IGError as e:
            logger.warning("Instagram postlarini olib bo'lmadi: %s", e)
        except Exception:
            logger.exception("Instagram kuzatuvi xatosi")
        await asyncio.sleep(interval)
