"""Mashina kartasi (admin uchun): matn, tugmalar, adminlarga yuborish."""

from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarSource, CarStatus, ListingSubmission
from bot.services.car_parser import ParsedCar

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    CarStatus.REVIEW: "🟡 Tekshiruv kerak",
    CarStatus.ACTIVE: "🟢 Sotuvda",
    CarStatus.RESERVED: "🔵 Bron",
    CarStatus.SOLD: "🔴 Sotildi",
    CarStatus.ARCHIVED: "⚪ Arxiv",
}

_FIELD_LABELS = {"brand": "marka", "model": "model", "year": "yil"}


def _fmt_int(n: int | None) -> str:
    return f"{n:,}".replace(",", " ") if n is not None else "—"


def channel_post_url(car: Car) -> str | None:
    if not car.channel_message_ids:
        return None
    un = (settings.channel_username or "").strip().lstrip("@")
    if un and not un.lstrip("-").isdigit():
        return f"https://t.me/{un}/{car.channel_message_ids[0]}"
    if car.channel_chat_id and str(car.channel_chat_id).startswith("-100"):
        return f"https://t.me/c/{str(car.channel_chat_id)[4:]}/{car.channel_message_ids[0]}"
    return None


def car_card_html(car: Car, *, header: str | None = None) -> str:
    e = html.escape
    lines: list[str] = []
    if header:
        lines.append(header)
        lines.append("")
    lines.append(f"🚗 <b>{e(car.title)}</b>  <code>#{car.id}</code>")
    price = f"${_fmt_int(car.price_usd)}" if car.price_usd else "narx yo'q"
    km = f"{_fmt_int(car.mileage_km)} km" if car.mileage_km is not None else "probeg yo'q"
    lines.append(f"💰 {price} · 🛣 {km}")
    specs = [p for p in (car.transmission, car.fuel, car.color, car.position) if p]
    if specs:
        lines.append("⚙️ " + " · ".join(e(s) for s in specs))
    cond = []
    if car.paint_status:
        cond.append(f"Kraska: {e(car.paint_status)}")
    if car.has_accident is not None:
        cond.append("DTP: " + ("bor" if car.has_accident else "yo'q"))
    if cond:
        lines.append("🖌 " + " · ".join(cond))
    if car.location:
        lines.append(f"📍 {e(car.location)}")
    if car.notes:
        lines.append(f"📝 <i>{e(car.notes[:400])}</i>")
    if car.is_own and car.purchase_price_usd:
        exp = car.expenses_usd or 0
        lines.append(f"🏷 Xarid: ${_fmt_int(car.purchase_price_usd)} + xarajat ${_fmt_int(exp)}")
    lines.append("")
    status = STATUS_LABELS.get(car.status, car.status)
    if car.status == CarStatus.REVIEW:
        missing = [lbl for f, lbl in _FIELD_LABELS.items() if not getattr(car, f)]
        if missing:
            status += f" (yetishmaydi: {', '.join(missing)})"
    lines.append(f"Holat: <b>{status}</b>")
    src = {CarSource.CHANNEL: "kanal", CarSource.BOT: "bot e'loni", CarSource.ADMIN: "admin", CarSource.IMPORT: "import"}
    meta = [f"manba: {src.get(car.source, car.source)}"]
    if car.ai_confidence is not None and car.source == CarSource.CHANNEL:
        meta.append(f"aniqlik: {round(car.ai_confidence * 100)}%")
    lines.append(f"<i>{' · '.join(meta)}</i>")
    url = channel_post_url(car)
    if url:
        lines.append(f'🔗 <a href="{e(url)}">Kanaldagi post</a>')
    return "\n".join(lines)


def car_admin_kb(car: Car) -> InlineKeyboardMarkup:
    cid = car.id
    rows: list[list[InlineKeyboardButton]] = []
    if car.status == CarStatus.REVIEW:
        rows.append(
            [
                InlineKeyboardButton(text="✅ To'g'ri, sotuvga", callback_data=f"car:ok:{cid}"),
                InlineKeyboardButton(text="✏️ Tuzatish", callback_data=f"car:edit:{cid}"),
            ]
        )
        rows.append([InlineKeyboardButton(text="🚫 E'lon emas", callback_data=f"car:arch:{cid}")])
    elif car.status in (CarStatus.ACTIVE, CarStatus.RESERVED):
        rows.append(
            [
                InlineKeyboardButton(text="💰 Sotildi", callback_data=f"car:sold:{cid}"),
                InlineKeyboardButton(text="✏️ Tuzatish", callback_data=f"car:edit:{cid}"),
            ]
        )
        if car.status == CarStatus.ACTIVE:
            rows.append([InlineKeyboardButton(text="🔵 Bron", callback_data=f"car:res:{cid}")])
        else:
            rows.append([InlineKeyboardButton(text="🟢 Bron bekor — sotuvda", callback_data=f"car:ok:{cid}")])
        rows.append([InlineKeyboardButton(text="🚫 Arxivga", callback_data=f"car:arch:{cid}")])
    else:
        if car_can_be_posted(car):
            rows.append([InlineKeyboardButton(text="📢 Kanalga joylash", callback_data=f"car:post:{cid}")])
        rows.append(
            [
                InlineKeyboardButton(text="↩️ Qayta sotuvga", callback_data=f"car:ok:{cid}"),
                InlineKeyboardButton(text="✏️ Tuzatish", callback_data=f"car:edit:{cid}"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def car_can_be_posted(car: Car) -> bool:
    """O'zimiz sotib olgan, rasmi bor, hali kanalda yo'q mashina — bot kanalga o'zi joylashi mumkin."""
    return bool(car.is_own and car.photo_file_ids and not car.channel_message_ids)


def car_channel_caption(car: Car) -> str:
    """Bot kanalga joylaydigan post matni (Real Avto shabloni)."""
    from bot.utils.contact_html import sales_phones_links_html

    e = html.escape
    lines = [f"🚗 <b>{e(' '.join(p for p in (car.brand, car.model) if p) or 'Mashina')}</b>"]
    if car.year:
        lines.append(f"📆 yili: {car.year}")
    if car.mileage_km is not None:
        lines.append(f"🛣 probeg: {_fmt_int(car.mileage_km)} km")
    specs = [p for p in (car.transmission, car.fuel, car.color, car.position) if p]
    if specs:
        lines.append("⚙️ " + " · ".join(e(s) for s in specs))
    if car.paint_status:
        lines.append(f"🖌 Kraska: {e(car.paint_status)}")
    if car.has_accident is not None:
        lines.append("DTP: " + ("bor" if car.has_accident else "yo'q"))
    lines.append(f"💰 Narxi: <b>${_fmt_int(car.price_usd)}</b>")
    lines.append(f"📍 {e(settings.business_name)} · {e(settings.business_address)}")
    lines.append(sales_phones_links_html())
    return "\n".join(lines)[:1024]


def stale_prompt_kb(car: Car) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Sotildi", callback_data=f"car:sold:{car.id}"),
                InlineKeyboardButton(text="✅ Hali sotuvda", callback_data=f"car:keep:{car.id}"),
            ],
            [InlineKeyboardButton(text="✏️ Narx/ma'lumotni tuzatish", callback_data=f"car:edit:{car.id}")],
        ]
    )


async def send_car_card_to_admins(
    bot: Bot,
    car: Car,
    *,
    header: str | None = None,
    with_photo: bool = True,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not settings.admin_telegram_ids:
        logger.warning("ADMIN_TELEGRAM_IDS bo'sh — mashina #%s kartasi yuborilmadi", car.id)
        return
    text = car_card_html(car, header=header)
    kb = reply_markup or car_admin_kb(car)
    photo = car.photo_file_ids[0] if (with_photo and car.photo_file_ids) else None
    for aid in settings.admin_telegram_ids:
        try:
            if photo and len(text) <= 1024:
                await bot.send_photo(aid, photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await bot.send_message(
                    aid, text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
                )
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("Admin %s ga mashina #%s kartasi yuborilmadi: %s", aid, car.id, e)


async def notify_admins_text(bot: Bot, text: str) -> None:
    for aid in settings.admin_telegram_ids:
        try:
            await bot.send_message(aid, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("Admin %s ga xabar yuborilmadi: %s", aid, e)


def parsed_from_listing(sub: ListingSubmission) -> ParsedCar:
    return ParsedCar(
        brand=sub.brand,
        model=sub.model,
        year=sub.year,
        mileage_km=sub.mileage,
        price_usd=sub.price_ask_usd,
        paint_status=sub.paint_status,
        has_accident=sub.has_accident,
        location=sub.location,
        notes=(sub.extra_details or None),
        confidence=1.0,
    )


async def car_from_approved_listing(
    cars: CarRepository,
    sub: ListingSubmission,
    *,
    channel_chat_id: int | None,
    channel_message_ids: list[int],
) -> Car:
    """Bot orqali berilgan va tasdiqlangan e'lonni mashinalar bazasiga qo'shish (agent shu bazadan javob beradi)."""
    existing = await cars.find_by_listing(sub.id)
    if existing is not None:
        return existing
    return await cars.create_from_parsed(
        parsed_from_listing(sub),
        source=CarSource.BOT,
        raw_text=sub.extra_details or "",
        photo_file_ids=list(sub.photo_file_ids or []),
        channel_chat_id=channel_chat_id,
        channel_message_ids=channel_message_ids,
        listing_submission_id=sub.id,
        status=CarStatus.ACTIVE,
    )
