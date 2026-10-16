"""Asosiy kanalni ochiq veb ko'rinishi (t.me/s/<kanal>) orqali kuzatish — AI'siz, avtomatik.

Bot kanal postlarini Telegram'dan jonli oladi; bu worker esa xavfsizlik to'ri:
  • har CHANNEL_SYNC_MINUTES daqiqada oxirgi 2 kun postlari:
      – bot o'tkazib yuborgan yangi e'lon (bot o'chiq edi va h.k.) → bazaga qo'shiladi
        (qayta tashlangani — mavjud mashinaga birlashtiriladi, arxivdagisi qayta sotuvga chiqadi);
      – postga «SOTILDI» yozilgan yoki telefon olib tashlangan → mashina «sotilgan»;
      – e'lon kanalda yana ko'rindi → «hali sotuvdami?» deb so'ralmaydi (kanal o'zi tasdiqlayapti);
  • kuniga bir marta (ish vaqtida) CAR_AUTO_ARCHIVE_DAYS kun ichida qayta chiqmagan kanal e'lonlari → arxiv
    (kanal sotilmagan mashinani muntazam qayta tashlaydi). Adminlarga bitta xulosa yuboriladi.
Veb sahifa to'liq o'qilmasa yoki juda ko'p mashina birdan «eskirgan» chiqsa — hech narsa arxivlanmaydi.
"""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import AppMeta, Car, CarSource, CarStatus
from bot.db.repositories import CrmRepository
from bot.services.car_cards import notify_admins_text, send_car_card_to_admins
from bot.services.car_parser import has_phone, is_sold_text, parse_car_text
from bot.services.channel_web import WebListing, WebPost, fetch_posts, listings
from bot.services.wishlist_notify import notify_wishlist_matches_car

logger = logging.getLogger(__name__)

RECENT_WINDOW = timedelta(days=2)
# Jonli ishlov berish (dumaloq videolarni eshitish va h.k.) tugashiga vaqt — undan yangi postlarga tegmaymiz
MIN_POST_AGE = timedelta(minutes=10)
# Bundan eski, bazada yo'q e'lonlar yaratilmaydi (eski tarix import skripti ishi)
CREATE_MAX_AGE = timedelta(days=3)
DEEP_EVERY = timedelta(hours=20)
DEEP_META_KEY = "channel_sync_deep_at"
MASS_ARCHIVE_SHARE = 0.5
MASS_ARCHIVE_MIN = 6
FIRST_RUN_AFTER_SECONDS = 90
_TASHKENT = timedelta(hours=5)
_WORK_HOURS_LOCAL = range(9, 21)
_OPEN = (CarStatus.ACTIVE, CarStatus.RESERVED, CarStatus.REVIEW)


@dataclass
class SyncResult:
    created: list[int] = field(default_factory=list)
    merged: list[int] = field(default_factory=list)
    reactivated: list[int] = field(default_factory=list)
    sold: list[int] = field(default_factory=list)


def _post_link(username: str, post_id: int) -> str:
    return f"https://t.me/{username.lstrip('@')}/{post_id}"


def _sold_reason(post: WebPost, car: Car | None) -> str | None:
    """Kanaldagi post hozir «sotilgan» ko'rinadimi: «SOTILDI» yozilgan yoki (avval bor) telefon olib tashlangan."""
    if is_sold_text(post.text):
        return "postga «SOTILDI» yozildi"
    old = (car.post_texts or {}).get(str(post.id)) if car is not None else None
    if old and has_phone(old) and not has_phone(post.text):
        return "postdan telefon raqami olib tashlandi"
    return None


async def _find_car(cars: CarRepository, chat_id: int, item: WebListing) -> Car | None:
    for mid in item.message_ids:
        car = await cars.find_by_channel_message(chat_id, mid)
        if car is not None:
            return car
    return None


async def apply_listings(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    items: list[WebListing],
    *,
    chat_id: int,
    username: str,
    now: datetime | None = None,
) -> SyncResult:
    now = now or datetime.now(UTC)
    res = SyncResult()
    for item in items:
        p = item.post
        if now - p.date < MIN_POST_AGE:
            continue
        parsed = parse_car_text(p.text, usd_rate_uzs=settings.usd_rate_uzs)
        if not (parsed.model or parsed.brand) or not parsed.looks_like_car():
            continue
        link = _post_link(username, p.id)
        async with session_factory() as session:
            cars = CarRepository(session)
            car = await _find_car(cars, chat_id, item)
            if car is not None:
                missing = [m for m in item.message_ids if m not in car.channel_message_ids]
                if missing:
                    car.channel_message_ids = [*car.channel_message_ids, *missing]
                reason = _sold_reason(p, car)
                if reason and car.status in _OPEN:
                    await cars.add_event(car, "web_sync_sold", {"post": p.id, "reason": reason})
                    await cars.set_status(car, CarStatus.SOLD)
                    await session.commit()
                    res.sold.append(car.id)
                    await send_car_card_to_admins(
                        bot,
                        car,
                        header=f'🔴 <b>Sotildi deb belgilandi</b> — <a href="{html.escape(link)}">kanaldagi post</a>: '
                        f"{reason}.\nXato bo'lsa: «↩️ Qayta sotuvga».",
                    )
                    continue
                # Kanal shu e'lonni yana ko'rsatyapti — «hali sotuvdami?» deb so'rash shart emas
                seen_again = not reason and car.status == CarStatus.ACTIVE and has_phone(p.text)
                if seen_again and (car.stale_prompted_at is None or car.stale_prompted_at < p.date):
                    car.stale_prompted_at = p.date
                await session.commit()
                continue

            # Bazada yo'q post. Telefonsiz/«sotildi» e'lon yoki eski post — yaratmaymiz
            if is_sold_text(p.text) or not has_phone(p.text) or now - p.date > CREATE_MAX_AGE:
                continue
            existing = await cars.find_repost_candidate(parsed, statuses=(*_OPEN, CarStatus.ARCHIVED))
            if existing is not None:
                was = existing.status
                existing.channel_message_ids = [*existing.channel_message_ids, *item.message_ids]
                existing.post_texts = {**(existing.post_texts or {}), str(p.id): p.text}
                if existing.channel_chat_id is None:
                    existing.channel_chat_id = chat_id
                existing.stale_prompted_at = p.date
                await cars.add_event(existing, "reposted", {"message_ids": item.message_ids, "via": "web"})
                if was == CarStatus.ARCHIVED:
                    await cars.set_status(existing, CarStatus.ACTIVE)
                await session.commit()
                if was == CarStatus.ARCHIVED:
                    res.reactivated.append(existing.id)
                    await send_car_card_to_admins(
                        bot,
                        existing,
                        header=f'♻️ <b>Yana sotuvda</b> — arxivdagi mashina <a href="{html.escape(link)}">kanalga qayta '
                        "joylandi</a>.",
                    )
                    await notify_wishlist_matches_car(bot, CrmRepository(session), cars, existing)
                    await session.commit()
                else:
                    res.merged.append(existing.id)
                continue
            car = await cars.create_from_parsed(
                parsed,
                source=CarSource.CHANNEL,
                raw_text=p.text,
                channel_chat_id=chat_id,
                channel_message_ids=item.message_ids,
                published_at=p.date,
            )
            car.post_texts = {str(p.id): p.text}
            car.stale_prompted_at = p.date
            await cars.add_event(car, "imported", {"from": "web_sync", "post": p.id})
            await session.commit()
            res.created.append(car.id)
            logger.info("Kanal sinxronizatsiyasi: post %s → yangi mashina #%s (%s)", p.id, car.id, car.title)
            await send_car_card_to_admins(
                bot,
                car,
                header=f'📥 <b>Kanaldagi e\'lon bazaga qo\'shildi</b> — <a href="{html.escape(link)}">post</a> '
                "bot o'chiq paytda chiqqan edi.",
            )
            if car.status == CarStatus.ACTIVE:
                await notify_wishlist_matches_car(bot, CrmRepository(session), cars, car)
                await session.commit()
    return res


def stale_channel_cars(cars: list[Car], posts: list[WebPost], *, cutoff: datetime) -> list[Car]:
    """`posts` — `cutoff` dan keyingi BARCHA postlar (to'liq o'qilgan). Shu oraliqda e'loni qayta chiqmagan mashinalar."""
    if not posts:
        return []
    seen = {mid for item in listings(posts) for mid in item.message_ids}
    first_id = min(p.id for p in posts)
    out = []
    for car in cars:
        ids = car.channel_message_ids or []
        if not ids or seen.intersection(ids):
            continue
        if max(ids) >= first_id:
            continue  # posti oraliqda bor, lekin e'lon emas yoki o'chirilgan — buni post_watch hal qiladi
        if car.published_at is not None and car.published_at > cutoff:
            continue
        if car.stale_prompted_at is not None and car.stale_prompted_at > cutoff:
            continue
        out.append(car)
    return out


async def archive_stale(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    posts: list[WebPost],
    *,
    chat_id: int,
    cutoff: datetime,
    days: int,
) -> list[int]:
    async with session_factory() as session:
        cars = CarRepository(session)
        active = await cars.channel_cars(chat_id)
        stale = stale_channel_cars(active, posts, cutoff=cutoff)
        if not stale:
            return []
        if len(stale) >= MASS_ARCHIVE_MIN and len(stale) > len(active) * MASS_ARCHIVE_SHARE:
            logger.warning("Kanal sinxronizatsiyasi: %s/%s mashina eskirgan chiqdi — arxivlanmadi", len(stale), len(active))
            await notify_admins_text(
                bot,
                f"⚠️ Kanal tekshiruvi: sotuvdagi {len(active)} ta mashinadan {len(stale)} tasi {days} kundan beri kanalda "
                "qayta chiqmagan ko'rinadi. Bu g'alati — xavfsizlik uchun hech narsa arxivlanmadi. Kanal o'zgarganmi?",
            )
            return []
        for car in stale:
            await cars.add_event(car, "auto_archived", {"days": days})
            await cars.set_status(car, CarStatus.ARCHIVED)
        await session.commit()
        lines = [f"• <code>#{c.id}</code> {html.escape(c.title)}" for c in stale[:30]]
    more = f"\n… va yana {len(stale) - 30} ta" if len(stale) > 30 else ""
    await notify_admins_text(
        bot,
        f"🗄 <b>{len(stale)} ta e'lon arxivga o'tdi</b> — {days} kundan beri kanalda qayta chiqmagan "
        "(katalog va AI endi taklif qilmaydi):\n" + "\n".join(lines) + more + "\n\n"
        "Hali sotuvda bo'lsa — kanalga qayta joylang (o'zi qaytadi) yoki CRM'da «Sotuvda» qiling.",
    )
    return [c.id for c in stale]


def _is_work_hours(now: datetime) -> bool:
    return (now + _TASHKENT).hour in _WORK_HOURS_LOCAL


async def _deep_due(session_factory: async_sessionmaker[AsyncSession], now: datetime) -> bool:
    async with session_factory() as session:
        row = await session.get(AppMeta, DEEP_META_KEY)
    if row is None:
        return True
    try:
        return now - datetime.fromisoformat(row.value) >= DEEP_EVERY
    except ValueError:
        return True


async def _mark_deep(session_factory: async_sessionmaker[AsyncSession], now: datetime) -> None:
    async with session_factory() as session:
        row = await session.get(AppMeta, DEEP_META_KEY)
        if row is None:
            session.add(AppMeta(key=DEEP_META_KEY, value=now.isoformat()))
        else:
            row.value = now.isoformat()
        await session.commit()


async def sync_once(bot: Bot, session_factory: async_sessionmaker[AsyncSession], *, deep: bool) -> SyncResult:
    username = (settings.channel_username or "").lstrip("@")
    chat_id = int(settings.channel_id)
    now = datetime.now(UTC)
    days = settings.car_auto_archive_days
    deep = deep and days > 0
    since = now - (timedelta(days=days) if deep else RECENT_WINDOW)
    posts, complete = await fetch_posts(username, since=since, max_pages=90 if deep else 6)
    res = await apply_listings(bot, session_factory, listings(posts), chat_id=chat_id, username=username, now=now)
    if deep:
        if complete:
            archived = await archive_stale(bot, session_factory, posts, chat_id=chat_id, cutoff=since, days=days)
            await _mark_deep(session_factory, now)
            logger.info("Kanal sinxronizatsiyasi (to'liq): %d post o'qildi, arxivlandi %s", len(posts), archived or "yo'q")
        else:
            logger.info("Kanal sinxronizatsiyasi: sahifalar to'liq o'qilmadi — arxivlash keyinroq")
    if res.created or res.sold or res.reactivated:
        logger.info(
            "Kanal sinxronizatsiyasi: yangi %s, sotildi %s, qayta sotuvda %s, birlashtirildi %s",
            res.created, res.sold, res.reactivated, res.merged,
        )
    return res


async def channel_sync_loop(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    if settings.channel_sync_minutes <= 0 or not settings.channel_username:
        logger.info("Kanal sinxronizatsiyasi o'chiq")
        return
    interval = max(5, settings.channel_sync_minutes) * 60
    logger.info("Kanal sinxronizatsiyasi: @%s har %s daqiqada", settings.channel_username.lstrip("@"), interval // 60)
    await asyncio.sleep(FIRST_RUN_AFTER_SECONDS)
    while True:
        try:
            now = datetime.now(UTC)
            deep = _is_work_hours(now) and await _deep_due(session_factory, now)
            await sync_once(bot, session_factory, deep=deep)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Kanal sinxronizatsiyasi xatosi")
        await asyncio.sleep(interval)
