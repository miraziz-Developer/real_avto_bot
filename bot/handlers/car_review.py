"""/aniqlash — sotuvdagi mashinalarni birma-bir ko'rib chiqish: sotildi / sotuvda / arxiv.

Har bir mashina alohida kartada (kanaldagi posti havolasi bilan) chiqadi; tugma bosilgach karta natija bilan
yangilanadi va keyingi mashina o'zi keladi. «Sotuvda» bosilgani — kanal sinxronizatsiyasi uni
CAR_AUTO_ARCHIVE_DAYS kun arxivlamaydi va «hali sotuvdami?» deb so'ralmaydi.
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from bot.config import is_admin, settings
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarStatus
from bot.db.repositories import CrmRepository
from bot.services.car_cards import car_card_html
from bot.services.wishlist_notify import notify_wishlist_matches_car

logger = logging.getLogger(__name__)
router = Router(name="car_review")

REVIEW_STATUSES = (CarStatus.ACTIVE, CarStatus.REVIEW, CarStatus.RESERVED)
_ACTIONS = {
    "sold": (CarStatus.SOLD, "💰 Sotildi"),
    "keep": (CarStatus.ACTIVE, "✅ Sotuvda"),
    "arch": (CarStatus.ARCHIVED, "🗄 Arxivga"),
}


async def _next_car(cars: CarRepository, after_id: int) -> tuple[Car | None, int]:
    """`after_id` dan keyingi ko'rilmagan mashina va undan keyin yana nechta qolgani."""
    cond = (Car.status.in_(REVIEW_STATUSES), Car.id > after_id)
    car = (await cars.session.execute(select(Car).where(*cond).order_by(Car.id).limit(1))).scalar_one_or_none()
    left = (await cars.session.execute(select(func.count()).select_from(Car).where(*cond))).scalar_one()
    return car, int(left)


def _post_link(car: Car) -> str | None:
    username = (settings.channel_username or "").lstrip("@")
    try:
        main_chat = int(settings.channel_id)
    except ValueError:
        return None
    if not username or car.channel_chat_id != main_chat or not car.channel_message_ids:
        return None
    return f"https://t.me/{username}/{max(car.channel_message_ids)}"


def review_kb(car: Car) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Sotildi", callback_data=f"rv:sold:{car.id}"),
                InlineKeyboardButton(text="✅ Sotuvda", callback_data=f"rv:keep:{car.id}"),
            ],
            [
                InlineKeyboardButton(text="🗄 Arxiv (eski e'lon)", callback_data=f"rv:arch:{car.id}"),
                InlineKeyboardButton(text="⏭ Keyingisi", callback_data=f"rv:skip:{car.id}"),
            ],
            [InlineKeyboardButton(text="⏹ To'xtatish", callback_data=f"rv:stop:{car.id}")],
        ]
    )


def review_text(car: Car, left: int) -> str:
    link = _post_link(car)
    when = f"\n📅 Kanalda: {car.published_at:%d.%m.%Y}" if car.published_at else ""
    post = f'\n🔗 <a href="{html.escape(link)}">Kanaldagi posti</a>' if link else ""
    header = f"🔎 <b>Aniqlash</b> — yana {left} ta qoldi{when}{post}\nShu mashina sotildimi?"
    return car_card_html(car, header=header)


async def _send_next(message: Message, cars: CarRepository, after_id: int) -> None:
    car, left = await _next_car(cars, after_id)
    if car is None:
        await message.answer(
            "✅ <b>Hammasi ko'rib chiqildi.</b> Katalogda endi faqat sotuvdagi mashinalar.\n"
            "Qaytadan boshlash: /aniqlash",
            parse_mode=ParseMode.HTML,
        )
        return
    await message.answer(
        review_text(car, left - 1), parse_mode=ParseMode.HTML, reply_markup=review_kb(car), disable_web_page_preview=True
    )


@router.message(Command("aniqlash"))
async def cmd_review(message: Message, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    total = (
        await cars.session.execute(select(func.count()).select_from(Car).where(Car.status.in_(REVIEW_STATUSES)))
    ).scalar_one()
    await message.answer(
        f"🔎 Sotuvdagi <b>{total}</b> ta mashinani birma-bir ko'rib chiqamiz.\n"
        "Har biriga: 💰 <b>Sotildi</b> · ✅ <b>Sotuvda</b> · 🗄 <b>Arxiv</b> (eski, endi sotilmaydi) · ⏭ keyinroq.\n"
        "Tugmani bosishingiz bilan keyingisi chiqadi.",
        parse_mode=ParseMode.HTML,
    )
    await _send_next(message, cars, 0)


@router.callback_query(F.data.startswith("rv:"))
async def review_action(cq: CallbackQuery, cars: CarRepository, crm: CrmRepository) -> None:
    if cq.from_user is None or not is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await cq.answer("Noto'g'ri tugma", show_alert=True)
        return
    action, car_id = parts[1], int(parts[2])
    msg = cq.message if isinstance(cq.message, Message) else None
    if action == "stop":
        await cq.answer("To'xtatildi")
        if msg is not None:
            await msg.edit_reply_markup(reply_markup=None)
            await msg.answer("⏹ To'xtatildi. Davom ettirish: /aniqlash")
        return
    car = await cars.get(car_id)
    if car is None:
        await cq.answer("Mashina topilmadi", show_alert=True)
        return
    result = "⏭ Keyinroq"
    if action in _ACTIONS:
        status, result = _ACTIONS[action]
        if action == "keep":
            await cars.mark_stale_prompted(car)  # tasdiqlandi — avtomatik arxiv/so'rov bir muddat kutadi
        changed = await cars.set_status(car, status, actor=cq.from_user.id)
        await cars.session.commit()
        if changed and status == CarStatus.ACTIVE and cq.bot is not None:
            await notify_wishlist_matches_car(cq.bot, crm, cars, car)
            await cars.session.commit()
    await cq.answer(result)
    if msg is None:
        return
    try:
        await msg.edit_text(
            car_card_html(car, header=f"<b>{result}</b> — <code>#{car.id}</code>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("Aniqlash kartasi yangilanmadi: %s", e)
    await _send_next(msg, cars, car_id)
