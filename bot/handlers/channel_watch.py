"""Asosiy kanalni kuzatish: har bir yangi post → mashinalar bazasi; tahrir/reply «SOTILDI» → status.

Bot kanalda admin bo'lishi kerak. Botning o'zi yuborgan postlar (tasdiqlangan bot e'lonlari) Telegram tomonidan
botga qaytib kelmaydi — ular ad_admin.listing_approve ichida bazaga yoziladi.
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Router
from aiogram.types import Chat, Message

from bot.ai import AIError, get_ai
from bot.config import settings
from bot.db.base import get_session_factory
from bot.db.cars_repo import CarRepository
from bot.db.models import CarSource, CarStatus
from bot.services.car_cards import car_card_html, notify_admins_text, send_car_card_to_admins
from bot.services.car_extract import extract_car
from bot.db.repositories import CrmRepository
from bot.services.car_parser import has_phone, is_reserved_text, is_sold_text
from bot.services.wishlist_notify import notify_wishlist_matches_car

logger = logging.getLogger(__name__)

router = Router(name="channel_watch")

# Albom xabarlari alohida-alohida keladi — shuncha soniya kutib, bitta e'lon sifatida yig'amiz
ALBUM_WAIT_SECONDS = 3.0
_MAX_TRANSCRIBE_BYTES = 19 * 1024 * 1024

_albums: dict[str, list[Message]] = {}
_album_tasks: dict[str, asyncio.Task] = {}


def is_main_channel(chat: Chat) -> bool:
    ref = (settings.channel_id or "").strip()
    if not ref:
        return False
    if ref.lstrip("-").isdigit():
        return chat.id == int(ref)
    return bool(chat.username) and ref.lstrip("@").lower() == chat.username.lower()


def _message_text(m: Message) -> str:
    return (m.caption or m.text or "").strip()


async def _transcribe_media(bot: Bot, messages: list[Message]) -> list[str]:
    ai = get_ai()
    if not ai.enabled:
        return []
    out: list[str] = []
    for m in messages:
        media = m.video_note or m.voice or m.audio or m.video
        if media is None:
            continue
        if (getattr(media, "file_size", 0) or 0) > _MAX_TRANSCRIBE_BYTES:
            continue
        try:
            f = await bot.get_file(media.file_id)
            buf = await bot.download_file(f.file_path)
            if buf is None:
                continue
            name = "audio.ogg" if m.voice else "video.mp4"
            text = await ai.transcribe(buf.read(), filename=name)
            if text:
                out.append(text)
        except AIError as e:
            logger.warning("Ovozni matnga aylantirib bo'lmadi (msg %s): %s", m.message_id, e)
        except Exception:
            logger.exception("Media yuklab olish/transkripsiya xatosi (msg %s)", m.message_id)
    return out


# Dumaloq video file_id lari shu prefiks bilan saqlanadi — mijozga send_video_note bilan qayta yuborish uchun
VIDEO_NOTE_PREFIX = "vn:"


def _media_of(messages: list[Message]) -> tuple[list[str], list[str]]:
    photos = [m.photo[-1].file_id for m in messages if m.photo]
    videos: list[str] = []
    for m in messages:
        if m.video_note is not None:
            videos.append(VIDEO_NOTE_PREFIX + m.video_note.file_id)
        if m.video is not None:
            videos.append(m.video.file_id)
    return photos, videos


async def _apply_reply_to_car(bot: Bot, cars: CarRepository, car, messages: list[Message], text: str) -> None:
    """Postga reply bilan qo'shilgan ma'lumot (narx, probeg, holat...) — mavjud mashinani yangilaydi."""
    transcripts = await _transcribe_media(bot, messages)
    full = "\n".join([text, *[f"[Ovoz]: {t}" for t in transcripts]]).strip()
    photos, videos = _media_of(messages)
    if not full and not (photos or videos):
        return
    was_active = car.status == CarStatus.ACTIVE
    changes: dict = {}
    if full:
        parsed = await extract_car(full, ai=get_ai(), usd_rate_uzs=settings.usd_rate_uzs)
        changes = await cars.apply_parsed(car, parsed, raw_text=f"{car.raw_text}\n{full}".strip())
    # Yangi list beramiz — ARRAY ustunidagi o'zgarish saqlanishi uchun
    new_ids = [m.message_id for m in messages if m.message_id not in car.channel_message_ids]
    car.channel_message_ids = [*car.channel_message_ids, *new_ids]
    if photos:
        car.photo_file_ids = [*car.photo_file_ids, *photos]
    if videos:
        car.video_file_ids = [*car.video_file_ids, *videos]
    await cars.session.commit()
    if changes or photos or videos:
        await send_car_card_to_admins(bot, car, header="✏️ <b>Kanaldagi reply bilan yangilandi</b>")
    if not was_active and car.status == CarStatus.ACTIVE:
        await notify_wishlist_matches_car(bot, CrmRepository(cars.session), cars, car)
        await cars.session.commit()


async def process_channel_post(bot: Bot, messages: list[Message]) -> None:
    """Bitta e'lon (oddiy post yoki albom) ni qayta ishlash. O'z DB sessiyasi bilan ishlaydi."""
    messages = sorted(messages, key=lambda m: m.message_id)
    first = messages[0]
    chat_id = first.chat.id
    msg_ids = [m.message_id for m in messages]
    text = "\n".join(t for t in (_message_text(m) for m in messages) if t)
    reply_to = next((m.reply_to_message for m in messages if m.reply_to_message), None)

    async with get_session_factory()() as session:
        cars = CarRepository(session)

        # «SOTILDI» / «baraka bo'ldi» deb reply qilingan — asl postdagi mashina sotildi
        if reply_to is not None and is_sold_text(text, extended=True):
            car = await cars.find_by_channel_message(chat_id, reply_to.message_id)
            if car is None:
                logger.info("Reply «sotildi», lekin bazada mashina topilmadi (msg %s)", reply_to.message_id)
                return
            if await cars.set_status(car, CarStatus.SOLD):
                await session.commit()
                await notify_admins_text(bot, f"🔴 Kanalda sotildi deb belgilandi:\n\n{car_card_html(car)}")
            return

        if reply_to is not None and is_reserved_text(text) and len(text) < 60:
            car = await cars.find_by_channel_message(chat_id, reply_to.message_id)
            if car is not None:
                if await cars.set_status(car, CarStatus.RESERVED):
                    await session.commit()
                    await send_car_card_to_admins(bot, car, header="🔵 <b>Kanalda bron deb belgilandi</b>")
                return

        if reply_to is not None:
            replied_car = await cars.find_by_channel_message(chat_id, reply_to.message_id)
            if replied_car is not None:
                # Masalan: dumaloq video ostiga «narxi 8500$» deb reply — shu mashinani to'ldiramiz
                await _apply_reply_to_car(bot, cars, replied_car, messages, text)
                return
            # Asl post (ko'pincha matnsiz dumaloq video) bazada yo'q — reply bilan birga bitta e'lon qilamiz
            messages = [reply_to, *messages]
            first = reply_to
            msg_ids = [m.message_id for m in messages]
            text = "\n".join(t for t in (_message_text(m) for m in messages) if t)

        if await cars.find_by_channel_message(chat_id, msg_ids[0]) is not None:
            return  # takroriy update

        transcripts = await _transcribe_media(bot, messages)
        full_text = "\n".join([text, *[f"[Ovoz]: {t}" for t in transcripts]]).strip()
        if not full_text:
            return  # matnsiz rasm/video — mashina ekanini aniqlab bo'lmaydi

        parsed = await extract_car(full_text, ai=get_ai(), usd_rate_uzs=settings.usd_rate_uzs)
        if not parsed.looks_like_car():
            logger.info("Kanal posti e'lon emas deb topildi (msg %s)", msg_ids[0])
            return

        photos, videos = _media_of(messages)
        status = CarStatus.SOLD if is_sold_text(text) else None
        car = await cars.create_from_parsed(
            parsed,
            source=CarSource.CHANNEL,
            raw_text=full_text,
            photo_file_ids=photos,
            video_file_ids=videos,
            channel_chat_id=chat_id,
            channel_message_ids=msg_ids,
            status=status,
            published_at=first.date,
        )
        await session.commit()
        if car.status == CarStatus.REVIEW:
            header = "🆕 <b>Kanalda yangi post</b> — ma'lumot to'liq emas, tekshirib bering"
        else:
            header = "🆕 <b>Kanalda yangi mashina</b>"
        await send_car_card_to_admins(bot, car, header=header)
        logger.info("Kanal posti → mashina #%s (%s, %s)", car.id, car.title, car.status)
        # «Chiqsa xabar ber» qidiruvini saqlagan mijozlarga
        await notify_wishlist_matches_car(bot, CrmRepository(session), cars, car)
        await session.commit()


async def _flush_album(bot: Bot, group_id: str) -> None:
    try:
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
        messages = _albums.pop(group_id, [])
        if messages:
            await process_channel_post(bot, messages)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Albomni qayta ishlash xatosi (%s)", group_id)
    finally:
        _album_tasks.pop(group_id, None)


@router.channel_post()
async def on_channel_post(message: Message, bot: Bot) -> None:
    if not is_main_channel(message.chat):
        return
    if message.media_group_id:
        gid = f"{message.chat.id}:{message.media_group_id}"
        _albums.setdefault(gid, []).append(message)
        if gid not in _album_tasks:
            _album_tasks[gid] = asyncio.create_task(_flush_album(bot, gid))
        return
    try:
        await process_channel_post(bot, [message])
    except Exception:
        logger.exception("Kanal postini qayta ishlash xatosi (msg %s)", message.message_id)


@router.edited_channel_post()
async def on_channel_post_edited(message: Message, bot: Bot, cars: CarRepository) -> None:
    if not is_main_channel(message.chat):
        return
    text = _message_text(message)
    car = await cars.find_by_channel_message(message.chat.id, message.message_id)
    if car is None:
        # Avval matnsiz bo'lgan post tahrirlanib e'longa aylangan bo'lishi mumkin
        if text:
            await process_channel_post(bot, [message])
        return

    old_text = car.raw_text or ""
    reason = None
    if is_sold_text(text, extended=True) and not is_sold_text(old_text, extended=True):
        reason = "postga «sotildi» yozildi"
    elif (
        text
        and message.message_id == car.channel_message_ids[0]
        and has_phone(old_text)
        and not has_phone(text)
        and car.status in (CarStatus.ACTIVE, CarStatus.RESERVED)
    ):
        # Jamoa odati: sotilgach postdan telefon raqami olib tashlanadi
        reason = "postdan telefon raqami olib tashlandi"
    if reason:
        if await cars.set_status(car, CarStatus.SOLD):
            car.raw_text = text
            await cars.session.commit()
            await send_car_card_to_admins(
                bot,
                car,
                header=f"🔴 <b>Sotildi deb belgilandi</b> — {reason}.\nXato bo'lsa: «↩️ Qayta sotuvga».",
            )
        return

    parsed = await extract_car(text, ai=get_ai(), usd_rate_uzs=settings.usd_rate_uzs)
    old_price = car.price_usd
    was_active = car.status == CarStatus.ACTIVE
    changes = await cars.apply_parsed(car, parsed, raw_text=text)
    if not changes:
        return
    await cars.session.commit()
    if not was_active and car.status == CarStatus.ACTIVE:
        # Tahrir mashinani to'liq qildi (tekshiruv → sotuvda) — «chiqsa xabar ber» egalariga
        await notify_wishlist_matches_car(bot, CrmRepository(cars.session), cars, car)
        await cars.session.commit()
    if "price_usd" in changes and old_price:
        await notify_admins_text(
            bot,
            f"💲 <b>{html.escape(car.title)}</b> <code>#{car.id}</code> narxi o'zgardi: "
            f"${old_price:,} → ${car.price_usd:,}",
        )
