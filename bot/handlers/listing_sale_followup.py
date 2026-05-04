"""Tasdiqlangan e'lon: sotilish holati, sharhlar kanali, kanal postida «SOTILDI»."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ContentType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import sale_followup_retry_hint, settings
from bot.db.repositories import CrmRepository
from bot.handlers.ad_listing import listing_caption_from_sub_public, truncate_caption_html
from bot.utils.contact_html import sales_phones_links_html

router = Router(name="listing_sale_followup")

logger = logging.getLogger(__name__)


class ListingSaleReviewStates(StatesGroup):
    waiting_review = State()


def _not_sold_message_html() -> str:
    sales_block = sales_phones_links_html()
    loc = settings.parking_location_text
    return (
        "📌 <b>Hozircha sotilmagan</b>\n\n"
        "Agar yordam kerak bo‘lsa, menejer bilan bog‘laning — "
        f"aloqa:\n{sales_block}\n\n"
        "Naqd yoki qulay shartlarda sotib olish imkoniyati, shuningdek "
        "parkingimizdagi avtomobillarni ko‘rib chiqish mumkin.\n\n"
        f"{loc}"
    )


async def _append_sold_to_channel_caption(bot, sub) -> None:
    if not sub.channel_message_id:
        return
    base = listing_caption_from_sub_public(sub)
    suffix = "\n\n✅ <b>SOTILDI</b>"
    cap = truncate_caption_html(base + suffix)
    try:
        await bot.edit_message_caption(
            chat_id=settings.channel_id,
            message_id=int(sub.channel_message_id),
            caption=cap,
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as e:
        logger.warning("Kanal kapsioni «SOTILDI» bilan tahrirlanmadi (#%s): %s", sub.id, e)


async def _forward_review_to_channel(bot, *, from_chat_id: int, message: Message, listing_id: int) -> None:
    ch = settings.reviews_channel_id
    try:
        await bot.send_message(
            ch,
            f"⭐ <b>Real Avto sharh</b> — e'lon <code>#{listing_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as e:
        logger.warning("Sharh kanaliga sarlavha yuborilmadi: %s", e)
        return
    try:
        await bot.forward_message(chat_id=ch, from_chat_id=from_chat_id, message_id=message.message_id)
        return
    except TelegramBadRequest:
        pass
    try:
        await bot.copy_message(chat_id=ch, from_chat_id=from_chat_id, message_id=message.message_id)
    except TelegramBadRequest as e:
        logger.warning("Sharh nusxalanmadi (e'lon #%s): %s", listing_id, e)


@router.callback_query(F.data.startswith("lfs:"))
async def listing_sale_callback(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return
    raw = (cq.data or "").strip()
    parts = raw.split(":")
    if len(parts) != 3 or parts[0] != "lfs" or not parts[1].isdigit():
        await cq.answer()
        return
    lid = int(parts[1])
    action = parts[2]
    uid = cq.from_user.id

    if action == "cancel":
        sub = await crm.try_revert_sale_feedback_pending(lid, user_telegram_id=uid)
        if sub is None:
            await cq.answer()
            return
        await state.clear()
        await cq.answer("Bekor qilindi.")
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        try:
            await cq.message.answer(
                "Sharh bekor qilindi. Keyinroq yana "
                f"{sale_followup_retry_hint(settings)} so'raymiz.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass
        return

    if action == "n":
        sub = await crm.try_set_sale_not_sold(lid, user_telegram_id=uid)
        if sub is None:
            await cq.answer("Bu e'lon uchun javob qabul qilinmadi.", show_alert=True)
            return
        await cq.answer()
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        try:
            await cq.message.answer(_not_sold_message_html(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except TelegramBadRequest:
            pass
        return

    if action == "y":
        sub = await crm.try_set_sale_feedback_pending(lid, user_telegram_id=uid)
        if sub is None:
            await cq.answer("Bu e'lon uchun «sotildi» bosilmadi yoki allaqachon yopilgan.", show_alert=True)
            return
        await cq.answer()
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await state.set_state(ListingSaleReviewStates.waiting_review)
        await state.update_data(lid=lid)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"lfs:{lid}:cancel")]],
        )
        try:
            await cq.message.answer(
                "🎉 <b>Tabriklaymiz!</b>\n\n"
                "Real Avto jamoasi uchun <b>iliq so‘z</b>, <b>qisqa video</b> yoki "
                "<b>ovozli xabar</b> yuboring — sharhingiz "
                "<b>Real Avto sharhlar</b> kanalida chop etiladi.\n\n"
                "<i>Bekor qilish: tugma yoki /cancel</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except TelegramBadRequest:
            pass
        return

    await cq.answer()


@router.message(Command("cancel"), StateFilter(ListingSaleReviewStates.waiting_review))
async def listing_sale_cmd_cancel(message: Message, state: FSMContext, crm: CrmRepository) -> None:
    data = await state.get_data()
    lid = data.get("lid")
    await state.clear()
    if isinstance(lid, int):
        await crm.try_revert_sale_feedback_pending(lid, user_telegram_id=message.from_user.id)
    try:
        await message.answer("Bekor qilindi.", parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass


_ALLOWED_REVIEW = frozenset(
    {
        ContentType.TEXT,
        ContentType.PHOTO,
        ContentType.VIDEO,
        ContentType.VIDEO_NOTE,
        ContentType.VOICE,
        ContentType.ANIMATION,
        ContentType.DOCUMENT,
    }
)


@router.message(StateFilter(ListingSaleReviewStates.waiting_review))
async def listing_sale_in_review_state(message: Message, state: FSMContext, crm: CrmRepository) -> None:
    if message.content_type not in _ALLOWED_REVIEW:
        try:
            await message.answer(
                "Iltimos, matn, rasm, video, dumaloq video yoki ovoz yuboring.\n"
                "Bekor: /cancel",
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass
        return

    data = await state.get_data()
    lid = data.get("lid")
    if not isinstance(lid, int):
        await state.clear()
        return
    uid = message.from_user.id if message.from_user else 0
    sub = await crm.try_finalize_sale_sold(lid, user_telegram_id=uid)
    if sub is None:
        await state.clear()
        try:
            await message.answer("Bu qadam endi amal qilmaydi. Yangi so‘rov kuting.")
        except TelegramBadRequest:
            pass
        return

    await _forward_review_to_channel(message.bot, from_chat_id=message.chat.id, message=message, listing_id=lid)
    await _append_sold_to_channel_caption(message.bot, sub)

    await state.clear()
    try:
        await message.answer(
            "🙏 <b>Rahmat!</b> Biz bilan ishlaganingiz uchun minnatdorligimizni bildiramiz.\n"
            "Sharhingiz sharhlar kanaliga yo‘naltirildi; asosiy kanaldagi "
            "e'lon matniga <b>SOTILDI</b> belgisi qo‘shildi.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        pass
