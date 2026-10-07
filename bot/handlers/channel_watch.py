"""Asosiy kanalni kuzatish: har bir yangi post → mashinalar bazasi; tahrir/reply «SOTILDI» → status.

Bot kanalda admin bo'lishi kerak. Botning o'zi yuborgan postlar (tasdiqlangan bot e'lonlari) Telegram tomonidan
botga qaytib kelmaydi — ular ad_admin.listing_approve ichida bazaga yoziladi.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time

from aiogram import Bot, Router
from aiogram.types import Chat, Message

from bot.ai import AIError, get_ai
from bot.config import settings
from bot.db.base import get_session_factory
from bot.db.cars_repo import CarRepository, same_car_model
from bot.db.models import CarSource, CarStatus
from bot.services.car_cards import notify_admins_text, send_car_card_to_admins
from bot.services.car_extract import extract_car
from bot.db.repositories import CrmRepository
from bot.services.car_parser import (
    has_phone,
    is_reserved_text,
    is_sold_confirmation,
    is_sold_text,
    is_sold_thanks,
    parse_car_text,
)
from bot.services.wishlist_notify import notify_wishlist_matches_car
from bot.workers.post_watch import HAS_MARKUP_EVENT

logger = logging.getLogger(__name__)

STATUS_TITLES = {CarStatus.RESERVED: "Bron qilindi", CarStatus.ACTIVE: "Yana sotuvda"}

router = Router(name="channel_watch")

# Albom xabarlari alohida-alohida keladi — shuncha soniya kutib, bitta e'lon sifatida yig'amiz
ALBUM_WAIT_SECONDS = 3.0
_MAX_TRANSCRIBE_BYTES = 19 * 1024 * 1024

_albums: dict[str, list[Message]] = {}
_album_tasks: dict[str, asyncio.Task] = {}

# Matnsiz media post (masalan dumaloq video) — ortidan kelgan matnli post bilan bitta e'lon qilish uchun.
# Forward qilinganda reply bog'lanishi yo'qoladi: video va uning tavsifi ketma-ket ikki post bo'lib keladi.
ORPHAN_MEDIA_SECONDS = 120
_orphan_media: dict[int, tuple[float, list[Message]]] = {}
# Gapirilgan video o'zi mashina bo'lib yaratilgan bo'lsa — ortidan kelgan tavsif shu mashinani to'ldiradi
_recent_media_car: dict[int, tuple[float, int]] = {}
# Bir kanal postlari navbat bilan: video transkripsiyasi (sekin) va tavsif (tez) tartibi buzilmasin
_channel_locks: dict[int, asyncio.Lock] = {}


def _channel_lock(chat_id: int) -> asyncio.Lock:
    return _channel_locks.setdefault(chat_id, asyncio.Lock())


def _peek_recent_media_car(chat_id: int) -> int | None:
    item = _recent_media_car.get(chat_id)
    if item is None:
        return None
    ts, car_id = item
    return car_id if time.monotonic() - ts <= ORPHAN_MEDIA_SECONDS else None


def _take_recent_media_car(chat_id: int) -> int | None:
    item = _recent_media_car.pop(chat_id, None)
    if item is None:
        return None
    ts, car_id = item
    return car_id if time.monotonic() - ts <= ORPHAN_MEDIA_SECONDS else None


def _take_orphan_media(chat_id: int) -> list[Message]:
    item = _orphan_media.pop(chat_id, None)
    if item is None:
        return []
    ts, msgs = item
    return msgs if time.monotonic() - ts <= ORPHAN_MEDIA_SECONDS else []


def _forget_orphan(chat_id: int, message_ids: list[int]) -> None:
    """Bu media reply orqali e'longa qo'shildi — keyingi postga yopishtirmaslik uchun buferdan olib tashlaymiz."""
    item = _orphan_media.get(chat_id)
    if item and any(m.message_id in message_ids for m in item[1]):
        _orphan_media.pop(chat_id, None)


def _same_post(new_text: str, old_text: str) -> bool:
    """Tahrirlangan matn shu mashina postining o'zimi (telefonsiz qolgan qismi eski matnga mos keladimi)."""
    words = [w for w in re.findall(r"\w+", new_text.lower()) if not w.isdigit() or len(w) == 4]
    if len(words) < 3:
        return False
    old_words = set(re.findall(r"\w+", old_text.lower()))
    return sum(w in old_words for w in words) / len(words) >= 0.7


def _original_date(m: Message):
    """Forward qilingan postda — asl post sanasi (statistika «necha kunda sotildi» to'g'ri bo'lishi uchun)."""
    origin_date = getattr(m.forward_origin, "date", None) if m.forward_origin else None
    return origin_date or m.date


def is_main_channel(chat: Chat) -> bool:
    ref = (settings.channel_id or "").strip()
    if not ref:
        return False
    if ref.lstrip("-").isdigit():
        return chat.id == int(ref)
    return bool(chat.username) and ref.lstrip("@").lower() == chat.username.lower()


def _message_text(m: Message) -> str:
    return (m.caption or m.text or "").strip()


# Kanal posti → muhokama guruhidagi avto-forward nusxasini bog'lash uchun tarkib kaliti.
# Boshqa kanaldan forward qilingan post guruhga tushganda forward_origin asl manbani ko'rsatadi
# (bizning kanal emas) — shunda postni media fayli yoki matni bo'yicha topamiz.
_POST_KEYS_MAX = 5000
_post_keys: dict[tuple[int, str], int] = {}


def content_key(m: Message) -> str | None:
    """Telegram'da forward qilinganda ham o'zgarmaydigan kalit: media file_unique_id yoki matn."""
    media = m.video_note or m.video or m.voice or m.audio or m.animation or m.document
    if media is not None:
        return f"f:{media.file_unique_id}"
    if m.photo:
        return f"f:{m.photo[-1].file_unique_id}"
    text = _message_text(m)
    return f"t:{' '.join(text.split())[:300]}" if text else None


def remember_post_content(m: Message) -> None:
    key = content_key(m)
    if key is None:
        return
    if len(_post_keys) >= _POST_KEYS_MAX:
        _post_keys.pop(next(iter(_post_keys)))
    _post_keys[(m.chat.id, key)] = m.message_id


def channel_post_by_content(channel_chat_id: int, m: Message) -> int | None:
    key = content_key(m)
    return _post_keys.get((channel_chat_id, key)) if key else None


def _media_mime(m: Message) -> str:
    if m.voice is not None:
        return m.voice.mime_type or "audio/ogg"
    if m.audio is not None:
        return m.audio.mime_type or "audio/mpeg"
    if m.video is not None:
        return m.video.mime_type or "video/mp4"
    return "video/mp4"  # dumaloq video


def _media_filename(m: Message) -> str:
    if m.voice is not None:
        return "audio.ogg"
    if m.audio is not None:
        return m.audio.file_name or "audio.mp3"
    return "video.mp4"


# (chat_id, message_id) → transkripsiya: bir video bir necha marta (pullik) qayta o'qilmasin
_transcript_cache: dict[tuple[int, int], str] = {}


async def _transcribe_media(bot: Bot, messages: list[Message]) -> list[str]:
    ai = get_ai()
    if not ai.enabled:
        return []
    out: list[str] = []
    for m in messages:
        media = m.video_note or m.voice or m.audio or m.video
        if media is None:
            continue
        key = (m.chat.id, m.message_id)
        if key in _transcript_cache:
            if _transcript_cache[key]:
                out.append(_transcript_cache[key])
            continue
        if (getattr(media, "file_size", 0) or 0) > _MAX_TRANSCRIBE_BYTES:
            continue
        try:
            f = await bot.get_file(media.file_id)
            buf = await bot.download_file(f.file_path)
            if buf is None:
                continue
            text = await ai.transcribe(buf.read(), filename=_media_filename(m), mime_type=_media_mime(m))
            if len(_transcript_cache) > 2000:
                _transcript_cache.clear()
            _transcript_cache[key] = text
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


async def _apply_reply_to_car(
    bot: Bot,
    cars: CarRepository,
    car,
    messages: list[Message],
    text: str,
    *,
    header: str = "✏️ <b>Kanaldagi reply bilan yangilandi</b>",
) -> None:
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
        if car.model and parsed.model and same_car_model(parsed.model, car.model):
            # «Lacetti» deb eshitildi, kanalda «Gentra» — mavjud nomni saqlaymiz
            parsed.brand, parsed.model = car.brand, car.model
        # Faqat ovoz (matnsiz video) — tekshiruvdagi mashinani o'zicha sotuvga chiqarmaydi
        changes = await cars.apply_parsed(
            car, parsed, raw_text=f"{car.raw_text}\n{full}".strip(), allow_activate=bool(text), merge_notes=True
        )
    # Yangi list beramiz — ARRAY ustunidagi o'zgarish saqlanishi uchun
    new_ids = [m.message_id for m in messages if m.message_id not in car.channel_message_ids]
    car.channel_message_ids = [*car.channel_message_ids, *new_ids]
    if photos:
        car.photo_file_ids = [*car.photo_file_ids, *photos]
    if videos:
        car.video_file_ids = [*car.video_file_ids, *videos]
    await cars.session.commit()
    if changes or photos or videos:
        if transcripts and not text:
            heard = html.escape(" ".join(transcripts)[:300])
            header += f"\n🎙 Eshitilgani ({_stt_name()}): <i>«{heard}»</i>"
        await send_car_card_to_admins(bot, car, header=header)
    if not was_active and car.status == CarStatus.ACTIVE:
        await notify_wishlist_matches_car(bot, CrmRepository(cars.session), cars, car)
        await cars.session.commit()


def _has_av(m: Message) -> bool:
    return bool(m.video_note or m.voice or m.audio or m.video)


def _post_link(chat: Chat, message_id: int) -> str | None:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    cid = str(chat.id)
    return f"https://t.me/c/{cid[4:]}/{message_id}" if cid.startswith("-100") else None


def _heard_line(heard: list[str]) -> str:
    return f"\n🎙 Eshitilgani ({_stt_name()}): <i>«{html.escape(' '.join(heard)[:300])}»</i>" if heard else ""


def _stt_name() -> str:
    """Kim eshitdi — admin transkripsiya sifatini baholay olsin (Groq Whisper o'zbekchani yomon taniydi)."""
    return "Gemini" if get_ai().provider == "gemini" else "Groq Whisper — o'zbekchani yomon taniydi, GEMINI_API_KEY qo'ying"


async def _car_for_old_post(bot: Bot, cars: CarRepository, post: Message):
    """Bazada yo'q (bot ulanmasdan oldingi) postga reply/tahrir keldi — mashinani shu postning o'zidan tiklaymiz.

    Telegram reply ichida asl postni to'liq beradi. Qayta joylangan nusxasi bazada bo'lsa — o'sha mashina.
    """
    text = _message_text(post)
    heard = await _transcribe_media(bot, [post]) if _has_av(post) else []
    full = "\n".join([text, *[f"[Ovoz]: {t}" for t in heard]]).strip()
    if not full:
        return None
    parsed = await extract_car(full, ai=get_ai(), usd_rate_uzs=settings.usd_rate_uzs)
    if not parsed.looks_like_car():
        return None
    existing = await cars.find_repost_candidate(parsed)
    if existing is not None:
        if post.message_id not in existing.channel_message_ids:
            existing.channel_message_ids = [*existing.channel_message_ids, post.message_id]
        return existing
    photos, videos = _media_of([post])
    text_only = parse_car_text(text, usd_rate_uzs=settings.usd_rate_uzs) if text else None
    unverified = bool(heard) and (text_only is None or not text_only.is_complete())
    car = await cars.create_from_parsed(
        parsed,
        source=CarSource.CHANNEL,
        raw_text=full,
        photo_file_ids=photos,
        video_file_ids=videos,
        channel_chat_id=post.chat.id,
        channel_message_ids=[post.message_id],
        status=CarStatus.REVIEW if unverified else None,
        published_at=_original_date(post),
    )
    await cars.add_event(car, "old_post_imported", {"message_id": post.message_id})
    logger.info("Eski post %s bazaga tiklandi → mashina #%s (%s)", post.message_id, car.id, car.title)
    return car


async def _status_by_reply(
    bot: Bot,
    cars: CarRepository,
    reply_to: Message,
    target: str,
    *,
    heard: list[str],
) -> None:
    """Postga «sotildi» / «bron» / tabrik videosi bilan reply — asl postdagi mashina (bazada yo'q bo'lsa tiklanadi)."""
    chat_id = reply_to.chat.id
    car = await cars.find_by_channel_message(chat_id, reply_to.message_id)
    restored = False
    if car is None:
        car = await _car_for_old_post(bot, cars, reply_to)
        restored = car is not None
    what = "sotildi" if target == CarStatus.SOLD else "bron"
    if car is None:
        link = _post_link(reply_to.chat, reply_to.message_id)
        link_line = f'\n<a href="{html.escape(link)}">Postga o\'tish</a>' if link else ""
        logger.info("Reply «%s», lekin mashina aniqlanmadi (msg %s)", what, reply_to.message_id)
        await notify_admins_text(
            bot,
            f"⚠️ Kanalda postga «{what}» deb javob yozildi, lekin post matnidan mashinani aniqlab bo'lmadi."
            f"{_heard_line(heard)}\nKerak bo'lsa: /sotildi &lt;id&gt; yoki /mashina{link_line}",
        )
        return
    changed = await cars.set_status(car, target)
    await cars.session.commit()
    if not (changed or restored):
        return
    note = "\n🗂 Eski post — bazaga endi qo'shildi." if restored else ""
    if target == CarStatus.SOLD:
        header = f"🔴 <b>Sotildi deb belgilandi</b> — kanalda postga javob.{note}{_heard_line(heard)}\nXato bo'lsa: «↩️ Qayta sotuvga»."
    else:
        header = f"🔵 <b>Kanalda bron deb belgilandi</b>{note}"
    await send_car_card_to_admins(bot, car, header=header)


async def _sold_by_announcement(
    bot: Bot,
    cars: CarRepository,
    parsed,
    messages: list[Message],
    *,
    heard: list[str],
    create_if_missing: bool,
    status_for_new: str | None = None,
    full_text: str = "",
) -> None:
    """Alohida «sotildi» posti yoki tabrik videosi (reply emas) — qaysi sotuvdagi mashina ekanini topamiz.

    Aniq moslik (model+yil+probeg/narx) yoki yumshoq moslikda bitta nomzod — sotildi. Bir nechta — adminlar
    tanlaydi (kartochkadagi «💰 Sotildi»). Topilmasa: «sotildi» postidan tarix uchun sotilgan yozuv yaratiladi,
    tabrik videosidan esa hech narsa yaratilmaydi (keraksiz «tekshiruv» mashinalari bo'lmasin).
    """
    chat_id = messages[0].chat.id
    existing = await cars.find_repost_candidate(parsed)
    candidates = [existing] if existing is not None else await cars.find_sold_candidates(parsed)
    if len(candidates) == 1:
        car = candidates[0]
        new_ids = [m.message_id for m in messages if m.message_id not in car.channel_message_ids]
        if existing is not None:
            car.channel_message_ids = [*car.channel_message_ids, *new_ids]
        await cars.add_event(car, "sold_announcement", {"message_ids": [m.message_id for m in messages]})
        changed = await cars.set_status(car, CarStatus.SOLD)
        await cars.session.commit()
        if changed:
            sure = "" if existing is not None else "\n❕ Faqat model bo'yicha topildi — tekshirib qo'ying."
            await send_car_card_to_admins(
                bot,
                car,
                header="🔴 <b>Sotildi deb belgilandi</b> — kanalda sotilgani haqida post chiqdi."
                f"{sure}{_heard_line(heard)}\nXato bo'lsa: «↩️ Qayta sotuvga».",
            )
        return
    link = _post_link(messages[0].chat, messages[0].message_id)
    link_line = f'\n<a href="{html.escape(link)}">Postga o\'tish</a>' if link else ""
    if len(candidates) > 1:
        await notify_admins_text(
            bot,
            f"🔴 Kanalda sotilgani haqida post: <b>{html.escape(' '.join(str(x) for x in (parsed.brand, parsed.model, parsed.year) if x))}</b>"
            f"{_heard_line(heard)}\nBazada {len(candidates)} ta mos mashina bor — qaysi biri sotilgan bo'lsa «💰 Sotildi» ni bosing:"
            f"{link_line}",
        )
        for car in candidates[:3]:
            await send_car_card_to_admins(bot, car, header="❓ <b>Shu mashina sotildimi?</b>")
        return
    if create_if_missing and parsed.looks_like_car():
        photos, videos = _media_of(messages)
        car = await cars.create_from_parsed(
            parsed,
            source=CarSource.CHANNEL,
            raw_text=full_text,
            photo_file_ids=photos,
            video_file_ids=videos,
            channel_chat_id=chat_id,
            channel_message_ids=[m.message_id for m in messages],
            status=status_for_new or CarStatus.SOLD,
            published_at=_original_date(messages[0]),
        )
        await cars.session.commit()
        await send_car_card_to_admins(bot, car, header="🔴 <b>Kanalda sotilgan mashina posti</b> — tarix uchun saqlandi")
        logger.info("Kanal posti → sotilgan mashina #%s (%s)", car.id, car.title)
        return
    logger.info("Sotildi/tabrik posti %s: mos mashina topilmadi", [m.message_id for m in messages])
    await notify_admins_text(
        bot,
        "🎥 Kanalda sotilgani haqida post/video chiqdi, lekin bazadan qaysi mashina ekanini topa olmadim."
        f"{_heard_line(heard)}\nSotilgan mashinani belgilash: /sotildi &lt;id&gt;{link_line}",
    )


async def process_channel_post(bot: Bot, messages: list[Message]) -> None:
    """Bitta e'lon (oddiy post yoki albom) ni qayta ishlash. O'z DB sessiyasi bilan ishlaydi."""
    async with _channel_lock(messages[0].chat.id):
        await _process_channel_post(bot, messages)


async def _process_channel_post(bot: Bot, messages: list[Message], *, edited: bool = False) -> None:
    messages = sorted(messages, key=lambda m: m.message_id)
    first = messages[0]
    chat_id = first.chat.id
    msg_ids = [m.message_id for m in messages]
    text = "\n".join(t for t in (_message_text(m) for m in messages) if t)
    reply_to = next((m.reply_to_message for m in messages if m.reply_to_message), None)

    async with get_session_factory()() as session:
        cars = CarRepository(session)

        if reply_to is not None:
            # Reply'dagi video/ovoz (masalan mijoz bilan «rahmat, muborak» dumaloq videosi) ham tinglanadi
            heard = await _transcribe_media(bot, messages) if any(_has_av(m) for m in messages) else []
            joined = " ".join(heard)
            # «SOTILDI» / «baraka bo'ldi» / «muborak» deb reply — asl postdagi mashina sotildi. Video ovozida
            # «barakasini bersin» e'lonning o'zida ham aytiladi — u yerda faqat aniq so'zlar (sotildi, muborak...)
            heard_sold = bool(heard) and not has_phone(joined) and (is_sold_text(joined) or is_sold_thanks(joined))
            if (is_sold_confirmation(text) or heard_sold) and len(text) < 300:
                await _status_by_reply(bot, cars, reply_to, CarStatus.SOLD, heard=heard)
                return
            if text and is_reserved_text(text) and len(text) < 60:
                await _status_by_reply(bot, cars, reply_to, CarStatus.RESERVED, heard=heard)
                return

        if reply_to is not None:
            _forget_orphan(chat_id, [reply_to.message_id])
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

        # Matnsiz video/ovoz: «sotildi», «mashinangiz muborak» — sotuvdan keyingi video, yangi mashina emas
        if not text and reply_to is None and any(_has_av(m) for m in messages):
            heard = await _transcribe_media(bot, messages)
            joined = " ".join(heard)
            if heard and not has_phone(joined) and (is_sold_text(joined) or is_sold_thanks(joined)):
                parsed = await extract_car(joined, ai=get_ai(), usd_rate_uzs=settings.usd_rate_uzs)
                await _sold_by_announcement(bot, cars, parsed, messages, heard=heard, create_if_missing=False)
                return

        # Matnsiz media (masalan 2-dumaloq video) ketma-ket kelsa — bitta mashinaga tegishli
        if not text and reply_to is None and any(m.photo or m.video or m.video_note for m in messages):
            recent_car_id = _peek_recent_media_car(chat_id)
            recent_car = await cars.get(recent_car_id) if recent_car_id else None
            if recent_car is not None:
                heard = parse_car_text(" ".join(await _transcribe_media(bot, messages)), usd_rate_uzs=settings.usd_rate_uzs)
                other_model = bool(heard.model and recent_car.model) and not same_car_model(heard.model, recent_car.model)
                if not other_model:
                    await _apply_reply_to_car(
                        bot, cars, recent_car, messages, "", header="🎥 <b>Shu mashinaga yana video qo'shildi</b>"
                    )
                    _recent_media_car[chat_id] = (time.monotonic(), recent_car.id)  # oyna uzayadi
                    return
                _recent_media_car.pop(chat_id, None)  # boshqa model — yangi mashina
            orphan = _take_orphan_media(chat_id)
            if orphan:
                # Oldingi matnsiz media bilan birga ko'rib chiqamiz (2 ta video — bitta mashina)
                messages = [*orphan, *messages]
                first = messages[0]
                msg_ids = [m.message_id for m in messages]

        transcripts = await _transcribe_media(bot, messages)
        full_text = "\n".join([text, *[f"[Ovoz]: {t}" for t in transcripts]]).strip()
        has_media = any(m.photo or m.video or m.video_note for m in messages)
        if not full_text:
            if has_media:
                # Matnsiz video/rasm — keyingi matnli post (tavsif) bilan birlashtiramiz
                _orphan_media[chat_id] = (time.monotonic(), messages)
            return

        parsed = await extract_car(full_text, ai=get_ai(), usd_rate_uzs=settings.usd_rate_uzs)
        if reply_to is None and text and not has_phone(full_text) and is_sold_thanks(text) and not is_sold_text(text):
            # «Mijozimizga Cobalt muborak!» — tabrik posti: sotuvdagini sotildi qilamiz, yangi yozuv yaratmaymiz
            await _sold_by_announcement(bot, cars, parsed, messages, heard=transcripts, create_if_missing=False)
            return
        if not parsed.looks_like_car():
            if has_media and not text:
                _orphan_media[chat_id] = (time.monotonic(), messages)
            logger.info(
                "Kanal posti e'lon emas deb topildi (msg %s): %r", msg_ids[0], full_text[:150].replace("\n", " ")
            )
            return

        if not has_media and reply_to is None:
            recent_car_id = _take_recent_media_car(chat_id)
            recent_car = await cars.get(recent_car_id) if recent_car_id else None
            if recent_car is not None:
                # Tavsif o'zidan oldingi (gapirilgan) videodan yaratilgan mashinaga tegishli
                _orphan_media.pop(chat_id, None)
                await _apply_reply_to_car(
                    bot, cars, recent_car, messages, text, header="📝 <b>Videodan keyingi tavsif posti qo'shildi</b>"
                )
                return
            orphan = _take_orphan_media(chat_id)
            if orphan:
                # Tavsif o'zidan oldingi matnsiz video/rasmga tegishli — bitta e'lon
                messages = [*orphan, *messages]
                first = messages[0]
                msg_ids = [m.message_id for m in messages]

        photos, videos = _media_of(messages)
        # Ovoz transkripsiyasi xato bo'lishi mumkin: asosiy faktlar (marka/model/yil) yoki narx faqat ovozdan
        # olingan bo'lsa — admin tasdiqlamaguncha sotuvga chiqmaydi (agent taklif qilmaydi)
        text_only = parse_car_text(text, usd_rate_uzs=settings.usd_rate_uzs) if text else None
        audio_unverified = bool(transcripts) and (
            text_only is None
            or not text_only.is_complete()
            or (parsed.price_usd is not None and text_only.price_usd is None)
        )
        low_confidence = parsed.confidence is not None and parsed.confidence < 0.6
        if is_sold_text(text):
            status = CarStatus.SOLD
        elif audio_unverified or low_confidence:
            status = CarStatus.REVIEW
        elif is_reserved_text(text):
            status = CarStatus.RESERVED  # «BRON» yozilgan post (ko'pincha eski post tahrirlanganda)
        else:
            status = None

        # «Cobalt 2020 sotildi ✅» — alohida yangi post (eski postga reply emas): sotuvdagi o'sha mashinani
        # sotildi qilamiz, yangi «sotilgan» yozuv yaratmaymiz (aks holda eski mashina sotuvda qolib ketardi)
        if status == CarStatus.SOLD:
            await _sold_by_announcement(
                bot, cars, parsed, messages, heard=transcripts, create_if_missing=True, full_text=full_text
            )
            return

        # Qayta tashlangan post (narx tushirib «ko'tarish», bot e'lonini qo'lda qayta joylash) — yangi mashina
        # yaratmaymiz: mavjudiga birlashtiramiz, aks holda agent bitta mashinani ikki marta taklif qiladi
        if status != CarStatus.SOLD:
            existing = await cars.find_repost_candidate(parsed)
            if existing is not None:
                await _merge_repost(bot, cars, existing, parsed, messages, full_text, allow_activate=status is None)
                if status == CarStatus.RESERVED and existing.status == CarStatus.ACTIVE:
                    await cars.set_status(existing, CarStatus.RESERVED)
                    await session.commit()
                return

        car = await cars.create_from_parsed(
            parsed,
            source=CarSource.CHANNEL,
            raw_text=full_text,
            photo_file_ids=photos,
            video_file_ids=videos,
            channel_chat_id=chat_id,
            channel_message_ids=msg_ids,
            status=status,
            published_at=_original_date(first),
        )
        if any(m.reply_markup for m in messages):
            # Boshqa bot orqali tugmali post — o'chirilganini tekshiruv uni chetlab o'tadi (tugmasi o'chmasin)
            await cars.add_event(car, HAS_MARKUP_EVENT)
        await session.commit()
        if has_media and not text:
            _recent_media_car[chat_id] = (time.monotonic(), car.id)
        else:
            _recent_media_car.pop(chat_id, None)
        if car.status == CarStatus.REVIEW and audio_unverified:
            heard = html.escape(" ".join(transcripts)[:300])
            header = (
                "🎙 <b>Ma'lumot videodagi ovozdan olindi</b> — to'g'riligini tekshirib tasdiqlang.\n"
                f"Eshitilgani ({_stt_name()}): <i>«{heard}»</i>"
            )
        elif car.status == CarStatus.REVIEW:
            header = "🆕 <b>Kanalda yangi post</b> — ma'lumot to'liq emas, tekshirib bering"
        elif edited:
            header = "✏️ <b>Eski post tahrirlandi</b> — mashina bazaga qo'shildi"
        else:
            header = "🆕 <b>Kanalda yangi mashina</b>"
        await send_car_card_to_admins(bot, car, header=header)
        logger.info("Kanal posti → mashina #%s (%s, %s)", car.id, car.title, car.status)
        # «Chiqsa xabar ber» qidiruvini saqlagan mijozlarga
        await notify_wishlist_matches_car(bot, CrmRepository(session), cars, car)
        await session.commit()


async def _merge_repost(
    bot: Bot,
    cars: CarRepository,
    car,
    parsed,
    messages: list[Message],
    full_text: str,
    *,
    allow_activate: bool,
) -> None:
    was_active = car.status == CarStatus.ACTIVE
    changes = await cars.apply_parsed(
        car, parsed, raw_text=f"{car.raw_text}\n{full_text}".strip(), allow_activate=allow_activate, merge_notes=True
    )
    new_ids = [m.message_id for m in messages if m.message_id not in car.channel_message_ids]
    car.channel_message_ids = [*car.channel_message_ids, *new_ids]
    if car.channel_chat_id is None and messages:
        car.channel_chat_id = messages[0].chat.id
    photos, videos = _media_of(messages)
    if photos and not car.photo_file_ids:
        car.photo_file_ids = photos
    if videos:
        car.video_file_ids = [*car.video_file_ids, *videos]
    await cars.add_event(car, "reposted", {"message_ids": new_ids})
    await cars.session.commit()
    header = f"♻️ <b>Kanalga qayta joylandi</b> — mavjud <code>#{car.id}</code> bilan birlashtirildi (dublikat yaratilmadi)"
    if "price_usd" in changes:
        header += "\n💰 Narx yangilandi"
    await send_car_card_to_admins(bot, car, header=header)
    logger.info("Kanal posti %s → mavjud mashina #%s ga birlashtirildi", [m.message_id for m in messages], car.id)
    if not was_active and car.status == CarStatus.ACTIVE:
        await notify_wishlist_matches_car(bot, CrmRepository(cars.session), cars, car)
        await cars.session.commit()


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
    remember_post_content(message)
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
    async with _channel_lock(message.chat.id):
        await _on_channel_post_edited(message, bot, cars)


async def _on_channel_post_edited(message: Message, bot: Bot, cars: CarRepository) -> None:
    text = _message_text(message)
    car = await cars.find_by_channel_message(message.chat.id, message.message_id)
    if car is None:
        # Avval matnsiz bo'lgan post tahrirlanib e'longa aylangan bo'lishi mumkin (lock allaqachon olingan)
        if text:
            logger.info("Bazada yo'q post %s tahrirlandi — qayta o'qiladi", message.message_id)
            await _process_channel_post(bot, [message], edited=True)
        return

    old_text = car.raw_text or ""
    reason = None
    if is_sold_confirmation(text) and not is_sold_confirmation(old_text):
        reason = "postga «sotildi» yozildi"
    elif (
        text
        and has_phone(old_text)
        and _same_post(text, old_text)
        and not has_phone(text)
        and car.status in (CarStatus.ACTIVE, CarStatus.RESERVED)
    ):
        # Jamoa odati: sotilgach postdan telefon raqami olib tashlanadi
        reason = "postdan telefon raqami olib tashlandi"
    if not reason and text and car.status in (CarStatus.ACTIVE, CarStatus.RESERVED):
        # Postga «BRON» yozildi yoki olib tashlandi
        now_reserved, was_reserved = is_reserved_text(text), is_reserved_text(old_text)
        target = None
        if now_reserved and not was_reserved and car.status == CarStatus.ACTIVE:
            target, why = CarStatus.RESERVED, "postga «bron» yozildi"
        elif was_reserved and not now_reserved and car.status == CarStatus.RESERVED:
            target, why = CarStatus.ACTIVE, "postdan «bron» olib tashlandi"
        if target is not None:
            await cars.set_status(car, target)
            car.raw_text = text
            await cars.session.commit()
            icon = "🔵" if target == CarStatus.RESERVED else "🟢"
            await send_car_card_to_admins(bot, car, header=f"{icon} <b>{STATUS_TITLES[target]}</b> — {why}.")
            return
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
    if car.status == CarStatus.ACTIVE and (not was_active or (old_price is None and car.price_usd)):
        # Tahrir mashinani sotuvga chiqardi yoki birinchi marta narx qo'ydi — «chiqsa xabar ber» egalariga
        await notify_wishlist_matches_car(bot, CrmRepository(cars.session), cars, car)
        await cars.session.commit()
    if "price_usd" in changes and old_price:
        await notify_admins_text(
            bot,
            f"💲 <b>{html.escape(car.title)}</b> <code>#{car.id}</code> narxi o'zgardi: "
            f"${old_price:,} → ${car.price_usd:,}",
        )
