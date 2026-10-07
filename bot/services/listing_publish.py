"""Bot orqali berilgan e'lonni kanalga joylash — admin tasdig'i, avtomatik joylash (muzlatish tugaganda)
va sotuvchi sotib olish taklifini rad etganda bir xil ishlaydi."""

from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import ListingSubmission
from bot.db.repositories import CrmRepository
from bot.handlers.ad_listing import listing_caption_from_sub_public, truncate_caption_html
from bot.services.car_cards import car_from_approved_listing
from bot.services.wishlist_notify import notify_wishlist_matches

logger = logging.getLogger(__name__)


class PublishError(Exception):
    """Kanalga yuborib bo'lmadi — adminga ko'rsatiladigan HTML matn bilan."""

    def __init__(self, html_text: str) -> None:
        super().__init__(html_text)
        self.html_text = html_text


async def publish_listing(
    bot: Bot,
    crm: CrmRepository,
    cars: CarRepository,
    sub: ListingSubmission,
    *,
    auto: bool = False,
) -> list[Message] | None:
    """E'lonni kanalga joylaydi. Allaqachon hal qilingan bo'lsa None. Kanal xatosida PublishError.

    Qatorni qulflaydi (SELECT ... FOR UPDATE): ikki admin, admin + muzlatish worker'i yoki sotib olish oqimi
    bir vaqtda chaqirsa, ikkinchisi birinchisi tugashini kutadi va None oladi — e'lon kanalga ikki marta chiqmaydi.
    """
    locked = await crm.lock_pending_listing(sub.id)
    if locked is None:
        return None
    sub = locked
    lid = sub.id
    photos = list(sub.photo_file_ids or [])
    if not photos:
        raise PublishError("Rasmlar yo'q")
    caption = truncate_caption_html(listing_caption_from_sub_public(sub))
    media = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode="HTML")]
    media.extend(InputMediaPhoto(media=p) for p in photos[1:])

    try:
        msgs = await bot.send_media_group(settings.channel_id, media)
    except TelegramBadRequest as e:
        logger.exception("Kanalga e'lon yuborish: %s", e)
        hint = ""
        if "chat not found" in str(e).lower():
            hint = (
                "\n\n<b>Nima qilish kerak</b>\n"
                "• <code>.env</code> dagi <code>CHANNEL_ID</code> — "
                "tasdiqlangan e'lonlar shu kanalga chiqadi (<code>-100…</code> yoki <code>@kanal</code>).\n"
                "• Botni shu kanalga qo‘shing va <b>Post messages</b> (yoki admin) huquqi bo‘lsin.\n"
                "• ID ni @RawDataBot / @getidsbot orqali kanaldan oling."
            )
        raise PublishError(f"❌ Kanal xatosi: {html.escape(str(e))}{hint}") from e

    first_id = msgs[0].message_id if msgs else None
    updated = await crm.try_mark_listing_approved(lid, channel_message_id=first_id)
    if updated is None:
        # Boshqa admin (yoki worker) bir vaqtda joylagan — dublikatni o'chiramiz
        for m in msgs:
            try:
                await bot.delete_message(settings.channel_id, m.message_id)
            except TelegramBadRequest:
                pass
        return None
    updated.frozen_until = None
    updated.auto_published = auto
    # Tasdiqni darhol saqlaymiz (qulf bo'shaydi); keyingi Telegram so'rovlari xato bersa ham
    # bazadagi holat kanal bilan mos qoladi.
    await crm.session.commit()

    bot_un = ""
    try:
        bot_un = ((await bot.get_me()).username or "").strip().lstrip("@")
    except Exception:
        logger.exception("get_me muvaffaqiyatsiz — kanal ostidagi savol havolasi yuborilmaydi")

    if bot_un and first_id is not None:
        try:
            await bot.send_message(
                settings.channel_id,
                "💬 <b>Mashina haqida savol</b> — bot orqali yozishingiz mumkin.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="💬 Savol yozish", url=f"https://t.me/{bot_un}?start=lq_{lid}")]]
                ),
                reply_to_message_id=first_id,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as e:
            logger.warning("Kanalga savol tugmasi yuborilmadi: %s", e)

    try:
        extra = ""
        if bot_un:
            ask_dm = f"https://t.me/{bot_un}?start=lq_{lid}"
            extra = '\n\nKanaldagi post ostida «<a href="' + html.escape(ask_dm) + '">Savol yozish</a>» tugmasi ham bor.'
        await bot.send_message(
            sub.user_telegram_id,
            "✅ E'loningiz tasdiqlandi va kanalda e'lon qilindi. Rahmat!" + extra,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        pass

    # Mashinalar bazasiga ham — savdo agenti va statistika shu bazadan ishlaydi.
    # Savepoint: xato bo'lsa ham e'lon tasdig'i (shu sessiyada) bekor bo'lmaydi
    try:
        async with cars.session.begin_nested():
            car = await car_from_approved_listing(
                cars,
                updated,
                channel_chat_id=msgs[0].chat.id if msgs else None,
                channel_message_ids=[m.message_id for m in msgs],
            )
            # Wishlist egalari pastda (e'lon yo'li bilan) xabar oladi — mashina orqali qayta yuborilmasin
            if not await cars.has_event(car, "wishlist_notified"):
                await cars.add_event(car, "wishlist_notified", {"via": "listing"})
    except Exception:
        logger.exception("Tasdiqlangan e'lon #%s mashinalar bazasiga yozilmadi", lid)

    try:
        await notify_wishlist_matches(bot, crm, updated)
    except Exception:
        logger.exception("Wishlist xabarnomalari (e'lon #%s)", lid)
    return msgs
