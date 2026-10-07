"""«Sotib olamiz»: muzlatish paytida jamoa sotuvchiga naqd narx taklif qiladi.

Oqim:
  admin «💰 Sotib olamiz» → narx yozadi → sotuvchiga taklif (✅ Roziman / 📢 Yo'q, e'lon qilinsin / 💬 Muhokama)
  • rozi yoki muhokama → adminlarga sotuvchi kontakti + «🏁 Sotib oldik» / «📢 Bekor — e'lon qilish»
  • rad → e'lon darhol kanalga
  • javob yo'q (BUYOUT_REPLY_HOURS) → worker e'lonni avtomatik kanalga chiqaradi
  • «🏁 Sotib oldik» → mashina bazaga «bizniki» (xarid narxi bilan) bo'lib tushadi, tayyor bo'lgach kartadan kanalga joylanadi
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import is_admin, settings
from bot.db.cars_repo import CarRepository
from bot.db.models import CarSource, CarStatus, ListingSubmission, ListingSubmissionStatus
from bot.db.repositories import CrmRepository
from bot.services.car_cards import car_admin_kb, car_card_html, notify_admins_text, parsed_from_listing
from bot.services.car_parser import parse_price_usd
from bot.services.listing_publish import PublishError, publish_listing
from bot.utils.currency import fmt_usd
from bot.utils.numbers import parse_db_id

logger = logging.getLogger(__name__)

router = Router(name="buyout")

BOUGHT_REASON = "Real Avto sotib oldi"


class BuyoutStates(StatesGroup):
    waiting_price = State()



def _lid(data: str | None) -> int | None:
    return parse_db_id((data or "").rsplit(":", 1)[-1])


# Narx «110 mln» kabi so'mda ham yozilishi mumkin — natija USD da; aql bovar qilmaydigan qiymatni rad etamiz
BUYOUT_PRICE_MAX_USD = 1_000_000


def _title(sub: ListingSubmission) -> str:
    return f"{sub.brand} {sub.model} {sub.year}"


def seller_offer_kb(lid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Roziman", callback_data=f"lbo:y:{lid}")],
            [InlineKeyboardButton(text="💬 Narxni muhokama qilaylik", callback_data=f"lbo:t:{lid}")],
            [InlineKeyboardButton(text="📢 Yo'q, e'lon qilinsin", callback_data=f"lbo:n:{lid}")],
        ]
    )


def deal_kb(lid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏁 Sotib oldik", callback_data=f"lbo_done:{lid}")],
            [InlineKeyboardButton(text="📢 Bekor — e'lon qilish", callback_data=f"lbo_pub:{lid}")],
        ]
    )


def _seller_contact_html(sub: ListingSubmission) -> str:
    parts = [f'👤 <a href="tg://user?id={sub.user_telegram_id}">Sotuvchi</a>']
    if sub.seller_username:
        parts.append(f"@{html.escape(sub.seller_username)}")
    if sub.phone:
        parts.append(f"<code>{html.escape(sub.phone)}</code>")
    return " · ".join(parts)


@router.callback_query(F.data.startswith("lad_b:"))
async def buyout_start(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    if cq.from_user is None or not is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    lid = _lid(cq.data)
    sub = await crm.get_listing_submission(lid or 0)
    if sub is None or sub.status != ListingSubmissionStatus.PENDING:
        await cq.answer("Bu e'lon allaqachon hal qilingan", show_alert=True)
        return
    if sub.buyout_status in ("offered", "accepted", "negotiating"):
        await cq.answer(f"Taklif allaqachon yuborilgan ({fmt_usd(sub.buyout_price_usd or 0)})", show_alert=True)
        return
    await cq.answer()
    await state.set_state(BuyoutStates.waiting_price)
    await state.update_data(buyout_lid=sub.id)
    if cq.message:
        await cq.message.answer(
            f"💰 <b>#{sub.id} {html.escape(_title(sub))}</b> — sotuvchi so'ragan narx: <b>{fmt_usd(sub.price_ask_usd)}</b>\n\n"
            "Sotuvchiga qancha naqd taklif qilamiz? Masalan: <code>8500</code> yoki <code>110 mln</code>\n"
            "Bekor qilish: /cancel",
            parse_mode=ParseMode.HTML,
        )


@router.message(StateFilter(BuyoutStates.waiting_price), Command("cancel"))
async def buyout_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi. E'lon muzlatishda qoldi.")


@router.message(StateFilter(BuyoutStates.waiting_price), F.text)
async def buyout_price(message: Message, state: FSMContext, bot: Bot, crm: CrmRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        await state.clear()
        return
    price = parse_price_usd(f"narx {message.text}", settings.usd_rate_uzs)
    if price is not None and not (0 < price <= BUYOUT_PRICE_MAX_USD):
        price = None
    if price is None:
        await message.answer(
            "Narxni tushunmadim. Masalan: <code>8500</code> yoki <code>110 mln</code>", parse_mode=ParseMode.HTML
        )
        return
    data = await state.get_data()
    await state.clear()
    # Qulf: shu paytda muzlatish worker'i e'lonni kanalga chiqarib yubormasin (yoki aksincha)
    sub = await crm.get_listing_submission(parse_db_id(str(data.get("buyout_lid") or "")) or 0, for_update=True)
    if sub is None or sub.status != ListingSubmissionStatus.PENDING:
        await message.answer("Bu e'lon endi hal qilingan.")
        return

    now = datetime.now(timezone.utc)
    hours = max(1, settings.buyout_reply_hours)
    text = (
        f"💰 <b>{html.escape(settings.business_name)} mashinangizni sotib olishga tayyor!</b>\n\n"
        f"🚗 {html.escape(_title(sub))}\n"
        f"💵 Taklif: <b>{fmt_usd(price)}</b> — naqd, tez, e'lon va kutishsiz.\n\n"
        "Rozimisiz?\n"
        f"<i>{hours} soat ichida javob bo'lmasa, e'loningiz odatdagidek kanalga chiqadi.</i>"
    )
    try:
        await bot.send_message(sub.user_telegram_id, text, parse_mode=ParseMode.HTML, reply_markup=seller_offer_kb(sub.id))
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await message.answer(
            f"❌ Sotuvchiga yuborilmadi (bot bloklangan bo'lishi mumkin): {html.escape(str(e))}",
            parse_mode=ParseMode.HTML,
        )
        return
    sub.buyout_status = "offered"
    sub.buyout_price_usd = price
    sub.buyout_admin_id = message.from_user.id
    sub.buyout_offered_at = now
    # Sotuvchi javobini kutamiz — shu vaqtgacha avtomatik joylanmaydi
    sub.frozen_until = now + timedelta(hours=hours)
    await crm.session.commit()
    await message.answer(
        f"✅ Taklif yuborildi: <b>{fmt_usd(price)}</b> (#{sub.id}). Sotuvchi {hours} soat ichida javob beradi, "
        "aks holda e'lon kanalga chiqadi.",
        parse_mode=ParseMode.HTML,
    )
    await notify_admins_text(
        bot,
        f"💰 {html.escape(message.from_user.first_name or 'Admin')} #{sub.id} {html.escape(_title(sub))} uchun "
        f"{fmt_usd(price)} taklif qildi — sotuvchi javobi kutilmoqda.",
    )


@router.callback_query(F.data.startswith("lbo:"))
async def seller_answer(cq: CallbackQuery, bot: Bot, crm: CrmRepository, cars: CarRepository) -> None:
    parts = (cq.data or "").split(":")
    lid = parse_db_id(parts[2]) if len(parts) == 3 else None
    if lid is None or cq.from_user is None:
        await cq.answer()
        return
    action = parts[1]
    # Qulf: «Roziman» va «Yo'q» ni ketma-ket tez bosish ikkala oqimni ham ishga tushirmasin
    sub = await crm.get_listing_submission(lid, for_update=True)
    if sub is None or sub.user_telegram_id != cq.from_user.id:
        await cq.answer("Bu taklif sizga tegishli emas", show_alert=True)
        return
    if sub.status != ListingSubmissionStatus.PENDING or sub.buyout_status != "offered":
        await cq.answer("Bu taklif endi amal qilmaydi", show_alert=True)
        return
    await cq.answer()
    if cq.message:
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    head = f"#{sub.id} {html.escape(_title(sub))} — taklif {fmt_usd(sub.buyout_price_usd or 0)}"

    if action in ("y", "t"):
        sub.buyout_status = "accepted" if action == "y" else "negotiating"
        # Avtomatik joylanmaydi (sotuvchi bizga sotmoqchi), lekin shu muddatda hal qilinmasa — adminlarga eslatma
        sub.frozen_until = datetime.now(timezone.utc) + timedelta(hours=max(1, settings.buyout_reply_hours))
        await crm.session.commit()
        if cq.message:
            await cq.message.answer("Rahmat! Menejerimiz tez orada siz bilan bog'lanadi ✅", parse_mode=None)
        title = "✅ <b>Sotuvchi ROZI!</b>" if action == "y" else "💬 <b>Sotuvchi narxni muhokama qilmoqchi</b>"
        text = f"{title}\n{head}\n{_seller_contact_html(sub)}\n\nBog'laning va kelishing."
        for aid in settings.admin_telegram_ids:
            try:
                await bot.send_message(aid, text, parse_mode=ParseMode.HTML, reply_markup=deal_kb(sub.id))
            except (TelegramBadRequest, TelegramForbiddenError):
                pass
        return

    # «Yo'q, e'lon qilinsin» — darhol kanalga
    sub.buyout_status = "declined"
    try:
        msgs = await publish_listing(bot, crm, cars, sub)
    except PublishError as e:
        await crm.session.commit()
        await notify_admins_text(bot, f"⚠️ {head}: sotuvchi rad etdi, lekin kanalga joylab bo'lmadi.\n{e.html_text}")
        return
    await crm.session.commit()
    if msgs is not None:
        await notify_admins_text(bot, f"ℹ️ {head}: sotuvchi rad etdi — e'lon kanalga joylandi.")


@router.callback_query(F.data.startswith("lbo_pub:"))
async def deal_publish(cq: CallbackQuery, bot: Bot, crm: CrmRepository, cars: CarRepository) -> None:
    if cq.from_user is None or not is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    sub = await crm.get_listing_submission(_lid(cq.data) or 0)
    if sub is None or sub.status != ListingSubmissionStatus.PENDING:
        await cq.answer("Bu e'lon allaqachon hal qilingan", show_alert=True)
        return
    await cq.answer()
    try:
        msgs = await publish_listing(bot, crm, cars, sub)
    except PublishError as e:
        if cq.message:
            await cq.message.answer(e.html_text, parse_mode=ParseMode.HTML)
        return
    if cq.message:
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await cq.message.answer("✅ E'lon kanalga joylandi." if msgs else "Bu e'lon allaqachon joylangan.")


@router.callback_query(F.data.startswith("lbo_done:"))
async def deal_bought(cq: CallbackQuery, crm: CrmRepository, cars: CarRepository) -> None:
    if cq.from_user is None or not is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    sub = await crm.get_listing_submission(_lid(cq.data) or 0)
    if sub is None or sub.status != ListingSubmissionStatus.PENDING:
        await cq.answer("Bu e'lon allaqachon hal qilingan", show_alert=True)
        return
    if await crm.try_mark_listing_rejected(sub.id, reason=BOUGHT_REASON) is None:
        await cq.answer("Bu e'lon allaqachon hal qilingan", show_alert=True)
        return
    sub.buyout_status = "bought"
    sub.frozen_until = None
    parsed = parsed_from_listing(sub)
    parsed.notes = f"{BOUGHT_REASON} — sotuvga tayyorlanmoqda. {sub.extra_details or ''}".strip()
    car = await cars.create_from_parsed(
        parsed,
        source=CarSource.BOT,
        raw_text=sub.extra_details or "",
        photo_file_ids=list(sub.photo_file_ids or []),
        listing_submission_id=sub.id,
        status=CarStatus.ARCHIVED,
    )
    await cars.update_fields(car, {"is_own": True, "purchase_price_usd": sub.buyout_price_usd}, actor=cq.from_user.id)
    await crm.session.commit()
    await cq.answer("🏁 Tabriklaymiz!")
    if cq.message:
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await cq.message.answer(
            car_card_html(
                car,
                header=(
                    "🏁 <b>Sotib olindi!</b> Mashina bazada «bizniki» bo'lib turibdi.\n"
                    "Ta'mir xarajati: «✏️ Tuzatish» → <code>xarajat 300</code>, sotuv narxi: <code>narx 9500</code>.\n"
                    "Tayyor bo'lgach «📢 Kanalga joylash»."
                ),
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=car_admin_kb(car),
            disable_web_page_preview=True,
        )
