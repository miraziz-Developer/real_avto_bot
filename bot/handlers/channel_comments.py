"""Kanal kommentlari va muhokama guruhi.

AI bor bo'lsa (COMMENTS_AI_ENABLED): har bir matn/ovoz xabarni AI o'qiydi va vaziyatga qarab — savolga javob,
salbiy fikrga xushmuomala javob + adminga signal, haqorat/spam/maqtovga javob yo'q (bot/services/comment_ai.py).
Faqat bazadagi faktlar ishlatiladi. AI yo'q / chegara tugagan — eski rejim: savolga bazadan shablon javob.
Batafsil suhbat botda davom etadi (`start=car_<id>`), u yerda agent ishlaydi.

Talab: bot kanalga ulangan muhokama guruhida ADMIN bo'lishi kerak (aks holda guruh xabarlarini ko'rmaydi).
"""

from __future__ import annotations

import html
import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageOriginChannel

from bot.ai import AIError, get_ai, get_budget
from bot.config import is_admin, settings
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarStatus
from bot.handlers.channel_watch import is_main_channel
from bot.services.car_cards import notify_admins_text
from bot.services.car_parser import detect_brand_model, normalize_text
from bot.services.comment_ai import CommentDecision, decide_comment_reply, public_post_text
from bot.utils.currency import fmt_price

logger = logging.getLogger(__name__)

router = Router(name="channel_comments")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

# Savol yoki qiziqish belgilari — «zo'r», «👍» kabi kommentlarga javob bermaymiz
_QUESTION_RE = re.compile(
    r"\?|narx|qancha|necha|bormi|bor\s*mi|sotil|kredit|nasiya|bo'lib|probeg|kraska|holat|manzil|qayer|"
    r"telefon|nomer|raqam|olaman|olsam|kelsam|ko'rsa|ko'rish|obmen|almash|"
    r"нарх|канча|борми|цена|сколько|есть\s*ли|продан|кредит|адрес|пробег|краск",
    re.IGNORECASE,
)
# Kuchli xarid niyati — menejerga darhol signal
_INTENT_RE = re.compile(
    r"olaman|olsam|kelsam|ko'rsa\s*bo'ladi|ko'rgani|kredit|nasiya|bo'lib\s*to'la|obmen|almash|nomer|raqam|telefon|"
    r"куплю|беру|кредит|приеду|номер",
    re.IGNORECASE,
)
REPLY_COOLDOWN_SECONDS = 600
_last_reply: dict[tuple[int, int], float] = {}
# AI rejimi: bir foydalanuvchiga bitta post/guruh bo'yicha 10 daqiqada ko'pi bilan shuncha javob
AI_REPLIES_PER_WINDOW = 3
_ai_replies: dict[tuple[int, int], list[float]] = {}
_HAS_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]")
_VOICE_MAX_BYTES = 19 * 1024 * 1024


def is_question(text: str | None) -> bool:
    return bool(text and _QUESTION_RE.search(normalize_text(text)))


def has_buy_intent(text: str | None) -> bool:
    return bool(text and _INTENT_RE.search(normalize_text(text)))


def _cooldown_ok(user_id: int, post_id: int) -> bool:
    """Bir mijozga bitta post bo'yicha 10 daqiqada bir marta — guruhni bot javoblari bilan to'ldirmaslik."""
    now = time.monotonic()
    key = (user_id, post_id)
    if now - _last_reply.get(key, float("-inf")) < REPLY_COOLDOWN_SECONDS:
        return False
    if len(_last_reply) > 20000:
        _last_reply.clear()
    _last_reply[key] = now
    return True



def _ai_window_ok(user_id: int, scope: int) -> bool:
    now = time.monotonic()
    key = (user_id, scope)
    hits = [t for t in _ai_replies.get(key, []) if now - t < REPLY_COOLDOWN_SECONDS]
    if len(hits) >= AI_REPLIES_PER_WINDOW:
        _ai_replies[key] = hits
        return False
    if len(_ai_replies) > 20000:
        _ai_replies.clear()
    _ai_replies[key] = [*hits, now]
    return True


def worth_reading(text: str | None) -> bool:
    """Bo'sh, faqat emoji/belgi yoki juda qisqa xabarlarni AI ga yubormaymiz (pul tejash)."""
    t = (text or "").strip()
    return len(t) >= 3 and bool(_HAS_LETTER_RE.search(t))


def message_link(message: Message) -> str | None:
    chat = message.chat
    if chat.username:
        return f"https://t.me/{chat.username}/{message.message_id}"
    cid = str(chat.id)
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{message.message_id}"
    return None


def comment_reply_text(car: Car | None, first_name: str | None) -> str:
    hello = f"Assalomu alaykum{', ' + html.escape(first_name) if first_name else ''}!"
    if car is None:
        return f"{hello} Savolingizga botda darhol javob beramiz 👇"
    title = html.escape(car.title)
    if car.status == CarStatus.ACTIVE:
        lines = [f"{hello} <b>{title}</b> hali sotuvda ✅"]
        facts = []
        if car.price_usd:
            facts.append(f"💰 <b>{fmt_price(car.price_usd)}</b>")
        if car.mileage_km is not None:
            facts.append(f"🛣 {car.mileage_km:,} km".replace(",", " "))
        if facts:
            lines.append(" · ".join(facts))
        lines.append("Rasmlar, savollar va ko'rishga yozilish — botda 👇")
        return "\n".join(lines)
    if car.status == CarStatus.RESERVED:
        return f"{hello} <b>{title}</b> hozir bron qilingan. O'xshash variantlarni botda ko'rsatamiz 👇"
    return f"{hello} Bu mashina sotilgan. O'xshash variantlarni botda ko'rsatamiz 👇"


async def _resolve_channel_post(message: Message, cars: CarRepository) -> tuple[int, int] | None:
    """Komment qaysi kanal postiga tegishli: avto-forwardga reply yoki thread orqali."""
    reply = message.reply_to_message
    if reply is not None and reply.is_automatic_forward and isinstance(reply.forward_origin, MessageOriginChannel):
        origin = reply.forward_origin
        if is_main_channel(origin.chat):
            return origin.chat.id, origin.message_id
    if message.message_thread_id:
        return await cars.channel_post_for_thread(message.chat.id, message.message_thread_id)
    return None


@router.message(F.is_automatic_forward)
async def on_auto_forward(message: Message, cars: CarRepository) -> None:
    """Kanal posti muhokama guruhiga tushdi — thread → post xaritasini saqlaymiz."""
    origin = message.forward_origin
    if not isinstance(origin, MessageOriginChannel) or not is_main_channel(origin.chat):
        return
    await cars.remember_thread(
        group_chat_id=message.chat.id,
        thread_message_id=message.message_id,
        channel_chat_id=origin.chat.id,
        channel_message_id=origin.message_id,
    )
    await cars.session.commit()


async def _is_our_group(message: Message, cars: CarRepository) -> bool:
    if settings.discussion_group_id is not None:
        return message.chat.id == settings.discussion_group_id
    return await cars.is_discussion_group(message.chat.id)


def _is_customer_message(message: Message) -> bool:
    """Bot javob beradigan xabar: oddiy foydalanuvchi yoki (sinash uchun) admin o'z savoli bilan."""
    user = message.from_user
    if user is None or user.is_bot:
        logger.debug("Guruh xabari %s: bot/anonim — o'tkazildi", message.message_id)
        return False
    if message.sender_chat is not None:
        logger.info("Guruh xabari %s: kanal/guruh nomidan yozilgan — o'tkazildi", message.message_id)
        return False
    if not is_admin(user.id):
        return True
    if not settings.comments_answer_admins:
        logger.info("Guruh xabari %s: admin yozdi (COMMENTS_ANSWER_ADMINS=false) — o'tkazildi", message.message_id)
        return False
    reply = message.reply_to_message
    if reply is not None and not reply.is_automatic_forward and not (reply.from_user and reply.from_user.is_bot):
        # Admin mijozga javob yozyapti — aralashmaymiz
        logger.info("Guruh xabari %s: admin boshqa xabarga javob yozdi — o'tkazildi", message.message_id)
        return False
    return True


def _from_admin(message: Message) -> bool:
    return message.from_user is not None and is_admin(message.from_user.id)


async def _notify_admins_about(
    bot: Bot, message: Message, car: Car | None, title: str, text: str, note: str = ""
) -> None:
    user = message.from_user
    who = html.escape(user.full_name) if user else "?"
    un = f" (@{html.escape(user.username)})" if user and user.username else ""
    about = f"{html.escape(car.title)} <code>#{car.id}</code>" if car else "guruh"
    link = message_link(message)
    link_line = f'\n<a href="{html.escape(link)}">Xabarga o\'tish</a>' if link else ""
    note_line = f"\n🤖 {html.escape(note)}" if note else ""
    await notify_admins_text(
        bot,
        f"{title}\n👤 {who}{un}\n🚗 {about}\n«{html.escape(text[:300])}»{note_line}{link_line}",
    )


async def _ai_handle(
    message: Message,
    bot: Bot,
    cars: CarRepository,
    bot_username: str,
    text: str,
    *,
    post: tuple[int, int] | None,
) -> bool:
    """AI qarori bo'yicha javob. True — xabar AI tomonidan ko'rib chiqildi (javob bo'ldimi-yo'qmi)."""
    ai = get_ai()
    if not settings.comments_ai_enabled or not ai.enabled or message.from_user is None:
        return False
    uid = message.from_user.id
    scope = post[1] if post else message.chat.id
    if not _ai_window_ok(uid, scope):
        logger.info("Komment %s: foydalanuvchi %s ga 10 daqiqada yetarli javob berildi — jim", message.message_id, uid)
        return True  # bu foydalanuvchiga yaqinda yetarlicha javob berdik — jim
    if not await get_budget().allow_user(f"tg:{uid}"):
        logger.info("Komment %s: foydalanuvchi %s kunlik AI limitiga yetdi", message.message_id, uid)
        return True
    car = await cars.find_by_channel_message(*post) if post else None
    other: list[Car] = []
    general = False
    if car is None or car.status not in (CarStatus.ACTIVE, CarStatus.RESERVED):
        brand, model = detect_brand_model(normalize_text(text))
        if brand or model:
            other = await cars.search_offerable(brand=brand, model=model, limit=5)
        elif car is None:
            # «Nima mashinalar bor?» kabi umumiy savol — sotuvdagilardan qisqa namuna (AI faqat kerak bo'lsa ishlatadi)
            other = await cars.search_offerable(limit=6)
            general = bool(other)
    replied = message.reply_to_message
    replied_text = None
    post_text = public_post_text(car.raw_text) if car else None
    if replied is not None and replied.is_automatic_forward:
        if not post_text:
            # Post bazada yo'q (eski post) — kanalda hamma ko'rgan post matnining o'zi
            post_text = public_post_text(replied.text or replied.caption) or None
    elif replied is not None:
        replied_text = replied.text or replied.caption
    try:
        decision: CommentDecision = await decide_comment_reply(
            ai,
            text=text,
            author=message.from_user.first_name,
            car=car,
            other_cars=other,
            post_text=post_text,
            replied_text=replied_text,
            general_inventory=general,
        )
    except AIError as e:
        logger.warning("Komment AI ishlamadi, shablon rejimi: %s", e)
        return False
    logger.info(
        "Komment AI: %s/%s (chat %s, msg %s, mashina=%s, mos=%d): %r",
        decision.category, decision.action, message.chat.id, message.message_id,
        car.id if car else None, len(other), text[:80],
    )

    if decision.should_reply:
        target = car or (other[0] if other and not general else None)
        deep = f"https://t.me/{bot_username}?start=car_{target.id}" if target else f"https://t.me/{bot_username}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤖 Botda batafsil", url=deep)]])
        await message.reply(decision.reply, parse_mode=None, reply_markup=kb, disable_web_page_preview=True)

    if _from_admin(message):
        return True  # admin o'zi sinab ko'ryapti — adminlarga signal shart emas
    if decision.category == "toxic":
        await _notify_admins_about(bot, message, car, "⚠️ <b>Guruhda haqorat / provokatsiya</b>", text, decision.admin_note)
    elif decision.category == "negative" or (decision.notify_admin and not decision.buy_intent):
        await _notify_admins_about(bot, message, car, "😟 <b>Guruhda salbiy fikr</b>", text, decision.admin_note)
    if decision.buy_intent:
        await _notify_admins_about(bot, message, car, "💬 <b>Kommentda xarid niyati</b>", text, decision.admin_note)
    return True


async def _template_handle(message: Message, bot: Bot, cars: CarRepository, bot_username: str, text: str, post) -> None:
    """AI'siz rejim: faqat savollarga, faqat post ostida — bazadan shablon javob."""
    if post is None or not is_question(text):
        return
    channel_chat_id, channel_msg_id = post
    if not _cooldown_ok(message.from_user.id, channel_msg_id):
        return
    car = await cars.find_by_channel_message(channel_chat_id, channel_msg_id)
    deep = f"https://t.me/{bot_username}?start=car_{car.id}" if car else f"https://t.me/{bot_username}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤖 Botda batafsil", url=deep)]])
    await message.reply(
        comment_reply_text(car, message.from_user.first_name),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    if has_buy_intent(text) and not _from_admin(message):
        await _notify_admins_about(bot, message, car, "💬 <b>Kommentda xarid niyati</b>", text)


async def _handle_group_text(message: Message, bot: Bot, cars: CarRepository, bot_username: str, text: str) -> None:
    post = await _resolve_channel_post(message, cars)
    if post is None and not await _is_our_group(message, cars):
        logger.info("Guruh %s bizning muhokama guruhimiz emas — xabar o'tkazildi", message.chat.id)
        return  # bot boshqa guruhga qo'shilgan bo'lsa — aralashmaymiz
    if worth_reading(text) and await _ai_handle(message, bot, cars, bot_username, text, post=post):
        return
    await _template_handle(message, bot, cars, bot_username, text, post)


@router.message(F.text)
async def on_comment(message: Message, bot: Bot, cars: CarRepository, bot_username: str) -> None:
    if not settings.comments_enabled or not _is_customer_message(message):
        return
    await _handle_group_text(message, bot, cars, bot_username, message.text or "")


@router.message(F.voice | F.video_note | (F.caption & (F.photo | F.video)))
async def on_media_comment(message: Message, bot: Bot, cars: CarRepository, bot_username: str) -> None:
    """Ovozli / dumaloq video komment (AI tinglaydi) yoki izohli rasm/video (izoh o'qiladi)."""
    if not settings.comments_enabled or not _is_customer_message(message):
        return
    if message.caption:
        await _handle_group_text(message, bot, cars, bot_username, message.caption)
        return
    ai = get_ai()
    media = message.voice or message.video_note
    if media is None or not settings.comments_ai_enabled or not ai.enabled:
        return
    if (media.file_size or 0) > _VOICE_MAX_BYTES:
        return
    post = await _resolve_channel_post(message, cars)
    if post is None and not await _is_our_group(message, cars):
        return
    if not await get_budget().allow_user(f"tg:{message.from_user.id}"):
        return
    try:
        f = await bot.get_file(media.file_id)
        buf = await bot.download_file(f.file_path)
        heard = await ai.transcribe(
            buf.read() if buf else b"",
            filename="audio.ogg" if message.voice else "video.mp4",
            mime_type=(message.voice.mime_type if message.voice else None) or ("audio/ogg" if message.voice else "video/mp4"),
        )
    except (AIError, TelegramBadRequest) as e:
        logger.warning("Guruhdagi ovozni o'qib bo'lmadi: %s", e)
        return
    if heard:
        await _handle_group_text(message, bot, cars, bot_username, heard)
