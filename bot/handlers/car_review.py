"""/aniqlash va /sotuvdagilar.

/aniqlash — sotuvdagi mashinalarni birma-bir ko'rib chiqish: sotildi / sotuvda / arxiv.

Har bir mashina alohida kartada (kanaldagi posti havolasi bilan) chiqadi; tugma bosilgach karta natija bilan
yangilanadi va keyingi mashina o'zi keladi. «Sotuvda» bosilgani — kanal sinxronizatsiyasi uni
CAR_AUTO_ARCHIVE_DAYS kun arxivlamaydi va «hali sotuvdami?» deb so'ralmaydi.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from bot.config import is_admin, settings
from bot.db.cars_repo import CarRepository
from bot.db.models import AppMeta, Car, CarEvent, CarStatus
from bot.db.repositories import CrmRepository
from bot.services.car_cards import car_card_html
from bot.services.channel_import import CONFIRMED_EVENT
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


# --- /sotuvdagilar: admin sotuvdagi postlarni forward qiladi, qolganlari sotuvdan olinadi ----------------------

ONSALE_META_KEY = "onsale_check_started_at"
_LIST_LIMIT = 40


def _onsale_kb(*, done: bool) -> InlineKeyboardMarkup:
    if not done:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Forward qilib bo'ldim", callback_data="os:done")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗄 Ularni sotuvdan olish (arxiv)", callback_data="os:arch")],
            [InlineKeyboardButton(text="💰 Ularni sotilgan deb belgilash", callback_data="os:sold")],
            [InlineKeyboardButton(text="🔎 Birma-bir ko'raman", callback_data="os:one")],
            [InlineKeyboardButton(text="✖️ Hech narsa qilmaslik", callback_data="os:cancel")],
        ]
    )


async def _started_at(cars: CarRepository) -> datetime | None:
    row = await cars.session.get(AppMeta, ONSALE_META_KEY)
    try:
        return datetime.fromisoformat(row.value) if row else None
    except ValueError:
        return None


async def onsale_split(cars: CarRepository, since: datetime) -> tuple[list[Car], list[Car]]:
    """Sotuvdagi mashinalar: `since` dan beri forward qilib tasdiqlangan va tasdiqlanmaganlar."""
    confirmed_ids = set(
        (
            await cars.session.execute(
                select(CarEvent.car_id).where(CarEvent.kind == CONFIRMED_EVENT, CarEvent.created_at >= since)
            )
        ).scalars()
    )
    rows = (
        await cars.session.execute(select(Car).where(Car.status.in_(REVIEW_STATUSES)).order_by(Car.id))
    ).scalars().all()
    confirmed = [c for c in rows if c.id in confirmed_ids]
    rest = [c for c in rows if c.id not in confirmed_ids]
    return confirmed, rest


@router.message(Command("sotuvdagilar"))
async def cmd_onsale(message: Message, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    now = datetime.now(UTC)
    row = await cars.session.get(AppMeta, ONSALE_META_KEY)
    if row is None:
        cars.session.add(AppMeta(key=ONSALE_META_KEY, value=now.isoformat()))
    else:
        row.value = now.isoformat()
    await cars.session.commit()
    await message.answer(
        "📋 <b>Sotuvdagilarni belgilash boshlandi.</b>\n\n"
        "1️⃣ Kanaldagi <b>hozir sotuvda turgan</b> har bir mashina e'lonini (tavsif postini) shu botga forward qiling.\n"
        "   Har biriga javob keladi: ✅ sotuvda / 🆕 bazaga qo'shildi.\n"
        "2️⃣ Hammasini yuborgach, pastdagi <b>«✅ Forward qilib bo'ldim»</b> ni bosing.\n"
        "3️⃣ Forward qilinmagan, lekin bazada «sotuvda» turganlar ro'yxati chiqadi — ularni nima qilishni siz tanlaysiz.\n"
        "   Siz tasdiqlamaguncha hech narsa o'chirilmaydi.",
        parse_mode=ParseMode.HTML,
        reply_markup=_onsale_kb(done=False),
    )


@router.callback_query(F.data.startswith("os:"))
async def onsale_action(cq: CallbackQuery, cars: CarRepository) -> None:
    if cq.from_user is None or not is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    action = (cq.data or "").split(":", 1)[1]
    msg = cq.message if isinstance(cq.message, Message) else None
    since = await _started_at(cars)
    if since is None:
        await cq.answer("Avval /sotuvdagilar ni bosing", show_alert=True)
        return
    confirmed, rest = await onsale_split(cars, since)
    if action == "cancel":
        await cq.answer("Hech narsa o'zgarmadi")
        if msg is not None:
            await msg.edit_reply_markup(reply_markup=None)
        return
    if action == "one":
        await cq.answer()
        if msg is not None:
            await msg.edit_reply_markup(reply_markup=None)
            await msg.answer("🔎 Birma-bir ko'rish: /aniqlash")
        return
    if not confirmed:
        await cq.answer("Hali birorta ham post forward qilinmagan", show_alert=True)
        return
    if action == "done":
        await cq.answer()
        if msg is None:
            return
        await msg.edit_reply_markup(reply_markup=None)
        if not rest:
            await msg.answer(f"✅ Sotuvda {len(confirmed)} ta mashina — hammasi forward qilingan. Baza tayyor.")
            return
        lines = [f"• <code>#{c.id}</code> {html.escape(c.title)}" for c in rest[:_LIST_LIMIT]]
        more = f"\n… va yana {len(rest) - _LIST_LIMIT} ta" if len(rest) > _LIST_LIMIT else ""
        await msg.answer(
            f"✅ Forward qilingan (sotuvda): <b>{len(confirmed)}</b> ta\n"
            f"❓ Bazada «sotuvda», lekin forward qilinmagan: <b>{len(rest)}</b> ta:\n"
            + "\n".join(lines) + more + "\n\nUlarni nima qilamiz?",
            parse_mode=ParseMode.HTML,
            reply_markup=_onsale_kb(done=True),
        )
        return
    if action in ("arch", "sold"):
        status = CarStatus.ARCHIVED if action == "arch" else CarStatus.SOLD
        for car in rest:
            await cars.add_event(car, "onsale_check", {"result": status})
            await cars.set_status(car, status, actor=cq.from_user.id)
        await cars.session.commit()
        await cq.answer("Bajarildi")
        if msg is not None:
            await msg.edit_reply_markup(reply_markup=None)
            what = "arxivga o'tdi" if action == "arch" else "sotilgan deb belgilandi"
            await msg.answer(
                f"✅ Tayyor: {len(rest)} ta mashina {what}.\n"
                f"🟢 Sotuvda endi: <b>{len(confirmed)}</b> ta (katalog va AI faqat shularni taklif qiladi).\n"
                "Xato bo'lsa: /mashina ID → «↩️ Qayta sotuvga». Yangi e'lonlarni bot o'zi qo'shib boradi.",
                parse_mode=ParseMode.HTML,
            )
        return
    await cq.answer("Noma'lum amal", show_alert=True)
