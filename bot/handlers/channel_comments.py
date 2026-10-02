"""Kanal kommentlari (muhokama guruhi): post ostidagi savolga bazadan qisqa javob + shu mashina bo'yicha botga havola.

Ochiq joyda AI emas — faqat bazadagi faktlar (narx, probeg, sotuvdami): xato javob xavfi yo'q.
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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageOriginChannel

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarStatus
from bot.handlers.channel_watch import is_main_channel
from bot.services.car_cards import notify_admins_text
from bot.services.car_parser import normalize_text
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


@router.message(F.text)
async def on_comment(message: Message, bot: Bot, cars: CarRepository, bot_username: str) -> None:
    if not settings.comments_enabled or message.from_user is None or message.from_user.is_bot:
        return
    if message.sender_chat is not None:
        return  # kanal/guruh nomidan yozilgan (admin) xabar
    if not is_question(message.text):
        return
    post = await _resolve_channel_post(message, cars)
    if post is None:
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
    if has_buy_intent(message.text):
        who = html.escape(message.from_user.full_name)
        un = f" (@{html.escape(message.from_user.username)})" if message.from_user.username else ""
        about = f"{html.escape(car.title)} <code>#{car.id}</code>" if car else "post"
        await notify_admins_text(
            bot,
            f"💬 <b>Kommentda xarid niyati</b>\n👤 {who}{un}\n🚗 {about}\n«{html.escape((message.text or '')[:300])}»",
        )
