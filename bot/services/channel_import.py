"""Kanal tarixini bot orqali import qilish (/import_kanal).

Telegram botga kanal tarixini bermaydi — bot faqat ulangandan keyingi postlarni ko'radi. Lekin bot kanal postini
ID bo'yicha forward qila oladi: post adminning bot bilan chatiga ovozsiz forward qilinadi, darhol o'chiriladi va
undan to'liq ma'lumot olinadi (matn, rasmlar, video, dumaloq video — ovozi Gemini bilan tinglanadi). Keyin post
xuddi yangi kelgandek odatiy kanal kuzatuvidan o'tadi: bitta mashinaning videolari va tavsifi birlashadi, qayta
joylanganlar dublikat bo'lmaydi, «SOTILDI» yozilganlari sotilgan bo'ladi.

Import paytida adminlarga kartalar va mijozlarga «chiqsa xabar ber» xabarlari yuborilmaydi — oxirida hisobot.
`active_days` dan eski, sotilganligi noma'lum mashinalar arxivga tushadi (agent eski mashinani taklif qilmasin).

Cheklov: forward qilingan postda reply bog'lanishi yo'qoladi — eski postlarga reply qilib yozilgan «sotildi»
xabarlari alohida «sotildi» e'loni sifatida ko'riladi (model bo'yicha topiladi, topilmasa o'tkazib yuboriladi).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Chat, Message, MessageOriginChannel
from sqlalchemy import func, select

from bot.db.base import get_session_factory
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarStatus
from bot.handlers import channel_watch
from bot.services.car_cards import notifications_muted
from bot.services.car_parser import has_phone
import contextlib

logger = logging.getLogger(__name__)

DEFAULT_COUNT = 300
MAX_COUNT = 3000
DEFAULT_ACTIVE_DAYS = 30
FORWARD_PAUSE_SECONDS = 0.4  # bitta chatga sekundiga ~2 ta forward+o'chirish — Telegram cheklovidan past
ALBUM_GAP_SECONDS = 3
PROGRESS_EVERY = 50
# «Telefonsiz post = sotilgan» qoidasi faqat kanalda telefon yozish odat bo'lsa ishlaydi
PHONE_RULE_MIN_POSTS = 5
PHONE_RULE_SHARE = 0.6

_POST_ID_RE = re.compile(r"(\d+)\s*$")
_running = False


@dataclass
class ImportReport:
    scanned: int = 0
    found: int = 0
    already_known: int = 0
    created: int = 0
    active: int = 0
    review: int = 0
    sold: int = 0
    sold_no_phone: int = 0
    archived: int = 0


def parse_post_ref(value: str) -> int | None:
    """«https://t.me/realavto/12345», «t.me/c/123/12345?single» yoki «12345» → 12345."""
    cleaned = value.strip().split("?")[0].rstrip("/")
    m = _POST_ID_RE.search(cleaned)
    if not m:
        return None
    mid = int(m.group(1))
    return mid if 0 < mid < 2**31 else None


def is_running() -> bool:
    return _running


async def _fetch_post(bot: Bot, channel: Chat, message_id: int, buffer_chat_id: int) -> Message | None:
    """Kanal postini ID bo'yicha o'qish: ovozsiz forward → nusxa → forward darhol o'chiriladi."""
    for _ in range(3):
        try:
            fwd = await bot.forward_message(buffer_chat_id, channel.id, message_id, disable_notification=True)
            break
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest:
            return None  # o'chirilgan, xizmat xabari yoki forward qilib bo'lmaydi
    else:
        return None
    with contextlib.suppress(TelegramBadRequest):
        await bot.delete_message(buffer_chat_id, fwd.message_id)
    origin = fwd.forward_origin
    return fwd.model_copy(
        update={
            "chat": channel,
            "message_id": message_id,
            "date": origin.date if origin is not None else fwd.date,
            "forward_origin": None,
            "reply_to_message": None,
            "media_group_id": None,
        }
    )


def _group_albums(posts: list[Message]) -> list[list[Message]]:
    """Forward qilinganda albom belgisi yo'qoladi: matnsiz rasm/video bir zumda oldingi rasmli postdan keyin
    kelgan bo'lsa — shu albomning davomi."""
    groups: list[list[Message]] = []
    for m in posts:
        prev = groups[-1] if groups else None
        if (
            prev is not None
            and (m.photo or m.video)
            and not (m.caption or m.text)
            and (prev[0].photo or prev[0].video)
            and m.message_id == prev[-1].message_id + 1
            and (m.date - prev[-1].date).total_seconds() <= ALBUM_GAP_SECONDS
        ):
            prev.append(m)
        else:
            groups.append([m])
    return groups


async def import_channel_history(
    bot: Bot,
    *,
    channel: Chat,
    last_id: int,
    count: int,
    buffer_chat_id: int,
    active_days: int = DEFAULT_ACTIVE_DAYS,
    progress=None,
) -> ImportReport:
    """Kanalning [last_id - count + 1, last_id] oralig'idagi postlarini bazaga import qiladi."""
    global _running
    if _running:
        raise RuntimeError("Import allaqachon ishlayapti")
    _running = True
    token = notifications_muted.set(True)
    report = ImportReport()
    try:
        async with get_session_factory()() as session:
            max_before = (await session.execute(select(func.coalesce(func.max(Car.id), 0)))).scalar_one()

        first_id = max(1, last_id - count + 1)
        posts: list[Message] = []
        for mid in range(first_id, last_id + 1):
            report.scanned += 1
            post = await _fetch_post(bot, channel, mid, buffer_chat_id)
            if post is not None:
                posts.append(post)
                channel_watch.remember_post_content(post)
            if progress is not None and report.scanned % PROGRESS_EVERY == 0:
                await progress(f"📥 O'qildi: {report.scanned}/{last_id - first_id + 1} (postlar: {len(posts)})")
            await asyncio.sleep(FORWARD_PAUSE_SECONDS)
        report.found = len(posts)

        prev_date: datetime | None = None
        for group in _group_albums(posts):
            async with get_session_factory()() as session:
                if await CarRepository(session).find_by_channel_message(channel.id, group[0].message_id):
                    report.already_known += 1
                    continue
            # Real vaqtda bir-biridan uzoq postlar (masalan video va 3 kundan keyingi tavsif) birlashmasin
            if prev_date is not None and (group[0].date - prev_date).total_seconds() > channel_watch.ORPHAN_MEDIA_SECONDS:
                channel_watch.forget_recent_media(channel.id)
            prev_date = group[-1].date
            try:
                await channel_watch.process_channel_post(bot, group)
            except Exception:
                logger.exception("Import: post %s ni qayta ishlab bo'lmadi", group[0].message_id)
        channel_watch.forget_recent_media(channel.id)

        cutoff = datetime.now(UTC) - timedelta(days=active_days)
        async with get_session_factory()() as session:
            repo = CarRepository(session)
            rows = (await session.execute(select(Car).where(Car.id > max_before).order_by(Car.id))).scalars().all()
            # Jamoa odati: sotilgach postdan telefon raqami o'chiriladi. Kanal postlarida odatda telefon bo'lsa,
            # telefonsiz qolgan e'lon — sotilgan (aks holda import sotilgan mashinani sotuvga qo'shib qo'yardi)
            texts_by_car = {c.id: [t for t in (c.post_texts or {}).values() if t] for c in rows}
            with_text = [cid for cid, texts in texts_by_car.items() if texts]
            with_phone = [cid for cid in with_text if any(has_phone(t) for t in texts_by_car[cid])]
            phones_are_usual = len(with_text) >= PHONE_RULE_MIN_POSTS and len(with_phone) >= PHONE_RULE_SHARE * len(with_text)
            for car in rows:
                if car.status not in (CarStatus.ACTIVE, CarStatus.REVIEW, CarStatus.RESERVED):
                    continue
                if car.published_at is not None and car.published_at < cutoff:
                    await repo.set_status(car, CarStatus.ARCHIVED)
                elif phones_are_usual and car.id in with_text and car.id not in with_phone:
                    await repo.set_status(car, CarStatus.SOLD)
                    await repo.add_event(car, "import_sold_no_phone")
                    report.sold_no_phone += 1
            await session.commit()
            for car in rows:
                report.created += 1
                if car.status == CarStatus.ACTIVE:
                    report.active += 1
                elif car.status == CarStatus.REVIEW:
                    report.review += 1
                elif car.status == CarStatus.SOLD:
                    report.sold += 1
                elif car.status == CarStatus.ARCHIVED:
                    report.archived += 1
        return report
    finally:
        notifications_muted.reset(token)
        _running = False


async def run_import_for_admin(
    bot: Bot, admin_chat_id: int, *, last_id: int, count: int, active_days: int
) -> None:
    """Fon vazifasi: import + adminga jarayon va hisobot."""

    async def progress(text: str) -> None:
        with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
            await bot.send_message(admin_chat_id, text, parse_mode=ParseMode.HTML)

    try:
        from bot.config import settings

        channel = await bot.get_chat(settings.channel_id)
        channel_chat = Chat(id=channel.id, type="channel", title=channel.title, username=channel.username)
        report = await import_channel_history(
            bot,
            channel=channel_chat,
            last_id=last_id,
            count=count,
            buffer_chat_id=admin_chat_id,
            active_days=active_days,
            progress=progress,
        )
    except Exception as e:
        logger.exception("Kanal importi xatosi")
        await progress(f"❌ Import to'xtadi: {html.escape(str(e))}")
        return
    await progress(
        "✅ <b>Kanal importi tugadi</b>\n"
        f"Ko'rildi: {report.scanned} ta ID, topilgan postlar: {report.found}\n"
        f"Bazada bor edi (o'tkazildi): {report.already_known}\n"
        f"Yangi mashinalar: {report.created}\n"
        f"  🟢 sotuvda: {report.active}\n"
        f"  🟡 tekshiruv kerak: {report.review} — /tekshiruv\n"
        f"  🔴 sotilgan: {report.sold} (shundan telefon raqami o'chirilgani uchun: {report.sold_no_phone})\n"
        f"  🗄 arxiv ({active_days} kundan eski): {report.archived}"
    )


# --- Admin postni botga forward qiladi → shu mashina bazaga qo'shiladi ------------------------------------
# Birinchi ulashda qaysi eski mashinalar hali sotuvda ekanini jamoa o'zi tanlaydi: postni botga forward qiladi.

FORWARD_ALBUM_WAIT_SECONDS = 2.0
_forward_albums: dict[str, list[Message]] = {}
_forward_tasks: dict[str, asyncio.Task] = {}


def channel_post_from_forward(m: Message) -> Message | None:
    """Asosiy kanaldan forward qilingan xabar → kanal posti ko'rinishida (asl ID va sana bilan)."""
    origin = m.forward_origin
    if not isinstance(origin, MessageOriginChannel) or not channel_watch.is_main_channel(origin.chat):
        return None
    chat = Chat(id=origin.chat.id, type="channel", title=origin.chat.title, username=origin.chat.username)
    return m.model_copy(
        update={
            "chat": chat,
            "message_id": origin.message_id,
            "date": origin.date,
            "forward_origin": None,
            "reply_to_message": None,
        }
    )


async def _answer(bot: Bot, chat_id: int, text: str) -> None:
    with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)


async def import_forwarded(bot: Bot, admin_chat_id: int, posts: list[Message]) -> None:
    """Forward qilingan kanal postini (albomni) bazaga qo'shish va adminga natijani aytish."""
    posts = sorted(posts, key=lambda m: m.message_id)
    channel_id = posts[0].chat.id
    async with get_session_factory()() as session:
        known = await CarRepository(session).find_by_channel_message(channel_id, posts[0].message_id)
    if known is not None:
        await _answer(
            bot, admin_chat_id,
            f"ℹ️ Bu post bazada bor: <b>{html.escape(known.title)}</b> <code>#{known.id}</code> — /mashina {known.id}",
        )
        return
    for p in posts:
        channel_watch.remember_post_content(p)
    await channel_watch.process_channel_post(bot, posts)
    async with get_session_factory()() as session:
        car = await CarRepository(session).find_by_channel_message(channel_id, posts[0].message_id)
    if car is None:
        if any(p.video_note or p.video or p.photo for p in posts) and not any(p.caption or p.text for p in posts):
            await _answer(bot, admin_chat_id, "🎥 Media qabul qilindi — endi shu mashinaning tavsif postini ham forward qiling.")
        else:
            await _answer(bot, admin_chat_id, "⚠️ Bu postdan mashina aniqlanmadi (e'lon emas).")
        return
    logger.info("Admin forward qildi: kanal posti %s → mashina #%s", posts[0].message_id, car.id)


def queue_forwarded(bot: Bot, admin_chat_id: int, message: Message) -> bool:
    """Albom qismlarini yig'ib, bitta e'lon qilib qo'shadi. False — bu kanal posti emas."""
    post = channel_post_from_forward(message)
    if post is None:
        return False
    if not message.media_group_id:
        task = asyncio.create_task(_safe_import(bot, admin_chat_id, [post]))
        _forward_tasks[f"single:{id(task)}"] = task
        task.add_done_callback(lambda t: _forward_tasks.pop(f"single:{id(t)}", None))
        return True
    key = f"{admin_chat_id}:{message.media_group_id}"
    _forward_albums.setdefault(key, []).append(post)
    if key not in _forward_tasks:
        _forward_tasks[key] = asyncio.create_task(_flush_forward_album(bot, admin_chat_id, key))
    return True


async def _flush_forward_album(bot: Bot, admin_chat_id: int, key: str) -> None:
    try:
        await asyncio.sleep(FORWARD_ALBUM_WAIT_SECONDS)
        posts = _forward_albums.pop(key, [])
        if posts:
            await _safe_import(bot, admin_chat_id, posts)
    finally:
        _forward_tasks.pop(key, None)


async def _safe_import(bot: Bot, admin_chat_id: int, posts: list[Message]) -> None:
    try:
        await import_forwarded(bot, admin_chat_id, posts)
    except Exception:
        logger.exception("Forward qilingan postni qo'shib bo'lmadi")
        await _answer(bot, admin_chat_id, "❌ Postni qo'shishda xato — logni tekshiring.")
