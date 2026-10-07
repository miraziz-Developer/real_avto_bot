"""E'lon bo'yicha savol-javob (matn + ovoz); raqam/@username filtrlash serverda."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from aiogram.exceptions import TelegramBadRequest

from bot.db.models import ListingSubmission, ListingThreadMessage
from bot.db.repositories import CrmRepository
from bot.keyboards import root_menu_keyboard
from bot.utils.anonym_guard import validate_anonymous_content
from bot.utils.contact_html import sales_phones_links_html
import contextlib

router = Router(name="listing_chat")
logger = logging.getLogger(__name__)


async def _answer_with_root_menu(message: Message, text: str) -> None:
    """Sotuvchi/xaridor oqimidan keyin — /start dagi asosiy menyuni qayta ko‘rsatish."""
    await message.answer(
        text,
        reply_markup=root_menu_keyboard(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


class ListingChatStates(StatesGroup):
    buyer_in_thread = State()
    seller_reply = State()


def _car_line(sub: ListingSubmission) -> str:
    return f"{html.escape(sub.brand)} {html.escape(sub.model)}, <b>{sub.year}</b> yil"


def _reply_button(thread_id: int, buyer_msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Javob berish",
                    callback_data=f"lt_r:{thread_id}:{buyer_msg_id}",
                )
            ],
        ],
    )


async def open_listing_buyer_entry(
    message: Message,
    state: FSMContext,
    crm: CrmRepository,
    listing_id: int,
) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id
    sub = await crm.get_approved_listing(listing_id)
    if sub is None:
        await message.answer("Bu e'lon topilmadi yoki hali kanalga chiqmagan (tasdiq kutilmoqda).")
        return
    if int(sub.user_telegram_id) == uid:
        await message.answer("Bu o'zingizning e'loningiz. Savollar boshqa foydalanuvchilar uchun.")
        return

    await crm.get_or_create_listing_thread(listing_id, uid)
    await state.set_state(ListingChatStates.buyer_in_thread)
    await state.update_data(lc_listing_id=listing_id)

    await message.answer(
        "💬 <b>Mashina haqida savol</b>\n\n"
        f"Mashina: {_car_line(sub)}\n\n"
        "Savolingizni <b>matn</b> yoki <b>ovoz</b> bilan yuboring.\n"
        "Telefon va boshqa bog‘lanish uchun Real Avto:\n"
        f"{sales_phones_links_html()}\n\n"
        "<i>Bu chatda javob shu yerda chiqadi.</i>\n\n"
        "Tugatgach: <b>Tugatdim</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Tugatdim", callback_data="lc_done")]],
        ),
    )


@router.callback_query(F.data == "lc_done", StateFilter(ListingChatStates.buyer_in_thread))
async def lc_buyer_done(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if cq.message:
        with contextlib.suppress(TelegramBadRequest):
            await cq.message.edit_reply_markup(reply_markup=None)
    await cq.answer("Yopildi.")
    if cq.message:
        await _answer_with_root_menu(
            cq.message,
            "<b>Savollar tugadi. Rahmat!</b>\n\n"
            "🏠 <b>Asosiy menyu</b> — davom etish uchun pastdagi tugmalardan foydalaning:",
        )


@router.message(StateFilter(ListingChatStates.buyer_in_thread), F.text)
async def lc_buyer_text(message: Message, state: FSMContext, crm: CrmRepository) -> None:
    if message.from_user is None:
        return
    body = (message.text or "").strip()
    if not body:
        return
    if body.startswith("/"):
        await message.answer(
            "Bu yerda komanda emas — oddiy matn yozing.\n"
            "Chiqish: «Tugatdim» tugmasi.",
            parse_mode=ParseMode.HTML,
        )
        return
    ok, err = validate_anonymous_content(body)
    if not ok:
        await message.answer(err or "Matn qabul qilinmadi.")
        return
    data = await state.get_data()
    lid = int(data.get("lc_listing_id") or 0)
    if lid < 1:
        await state.clear()
        return
    sub = await crm.get_approved_listing(lid)
    if sub is None:
        await state.clear()
        await message.answer("E'lon endi mavjud emas.")
        return
    if int(sub.user_telegram_id) == message.from_user.id:
        await state.clear()
        return

    th = await crm.get_or_create_listing_thread(lid, message.from_user.id)
    row = await crm.add_listing_thread_message(
        th.id,
        is_from_seller=False,
        body_text=body[:4000],
        voice_file_id=None,
        in_reply_to=None,
    )
    await _notify_seller_question(message.bot, crm, sub, th.id, row, body_preview=body[:800])
    await message.answer("✅ Savolingiz e'lon egasiga yuborildi. Yana yozishingiz yoki «Tugatdim».")


@router.message(StateFilter(ListingChatStates.buyer_in_thread), F.voice)
async def lc_buyer_voice(message: Message, state: FSMContext, crm: CrmRepository) -> None:
    if message.from_user is None or message.voice is None:
        return
    cap = message.caption
    if cap and cap.strip():
        ok, err = validate_anonymous_content(cap)
        if not ok:
            await message.answer(err or "Ovoz izohi qabul qilinmadi.")
            return
    data = await state.get_data()
    lid = int(data.get("lc_listing_id") or 0)
    if lid < 1:
        await state.clear()
        return
    sub = await crm.get_approved_listing(lid)
    if sub is None:
        await state.clear()
        await message.answer("E'lon endi mavjud emas.")
        return
    if int(sub.user_telegram_id) == message.from_user.id:
        await state.clear()
        return
    th = await crm.get_or_create_listing_thread(lid, message.from_user.id)
    fid = message.voice.file_id
    row = await crm.add_listing_thread_message(
        th.id,
        is_from_seller=False,
        body_text=cap.strip()[:500] if cap and cap.strip() else None,
        voice_file_id=fid,
        in_reply_to=None,
    )
    await _notify_seller_question(
        message.bot,
        crm,
        sub,
        th.id,
        row,
        body_preview=None,
        has_voice=True,
    )
    await message.answer("✅ Ovoz yuborildi. Yana yuborishingiz yoki «Tugatdim».")


async def _notify_seller_question(
    bot,
    crm: CrmRepository,
    sub,
    thread_id: int,
    row: ListingThreadMessage,
    *,
    body_preview: str | None,
    has_voice: bool = False,
) -> None:
    seller = int(sub.user_telegram_id)
    car = _car_line(sub)
    kb = _reply_button(thread_id, row.id)
    header = f"🔔 <b>Yangi savol</b> · e'lon <code>#{sub.id}</code>\n{car}"

    if has_voice and row.voice_file_id:
        cap_parts = [header, "🎤 <b>Ovoz ostidagi savol</b>"]
        if row.body_text:
            cap_parts.append(f"<i>{html.escape(row.body_text[:400])}</i>")
        cap_parts.append("<i>Javob: «Javob berish» tugmasi, keyin matn yoki ovoz.</i>")
        caption = "\n".join(cap_parts)
        if len(caption) > 1024:
            caption = caption[:1020] + "…"
        try:
            await bot.send_voice(
                seller,
                voice=row.voice_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return
        except TelegramBadRequest as e:
            logger.warning("Sotuvchiga ovozli savol yuborilmadi tg=%s: %s", seller, e)

    if has_voice:
        body_html = "🎤 <b>Ovozli xabar</b> (ijro qilish mumkin emas — qayta yuboring)"
        if row.body_text:
            body_html += f"\n<i>{html.escape(row.body_text[:400])}</i>"
    else:
        body_html = html.escape((body_preview or "")[:1200])

    text = (
        f"{header}\n\n"
        f"{body_html}\n\n"
        "<i>Javob shu savolga tegishli bo‘lsin — tugmani bosing, keyin matn/ovoz yuboring.</i>"
    )
    try:
        await bot.send_message(
            seller,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        logger.warning("Sotuvchiga savol yuborilmadi tg=%s: %s", seller, e)


@router.callback_query(F.data.startswith("lt_r:"))
async def lc_seller_start_reply(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    if cq.from_user is None:
        await cq.answer()
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await cq.answer()
        return
    thread_id, qid = int(parts[1]), int(parts[2])
    pair = await crm.get_thread_with_listing(thread_id)
    if pair is None:
        await cq.answer("Topilmadi", show_alert=True)
        return
    _th, sub = pair
    if int(sub.user_telegram_id) != cq.from_user.id:
        await cq.answer("Bu sizning e'loningiz emas.", show_alert=True)
        return
    qrow = await crm.get_thread_message(qid)
    if qrow is None or qrow.thread_id != thread_id or qrow.is_from_seller:
        await cq.answer("Savol topilmadi.", show_alert=True)
        return

    await state.set_state(ListingChatStates.seller_reply)
    await state.update_data(lc_reply_thread=thread_id, lc_reply_to=qid, lc_listing_id=sub.id)
    await cq.answer()
    if cq.message:
        with contextlib.suppress(TelegramBadRequest):
            await cq.message.edit_reply_markup(reply_markup=None)
        await cq.message.answer(
            "✍️ <b>Javob</b> — shu savolga matn yoki ovoz yuboring.",
            parse_mode=ParseMode.HTML,
        )


@router.message(StateFilter(ListingChatStates.seller_reply), F.text)
async def lc_seller_text(message: Message, state: FSMContext, crm: CrmRepository) -> None:
    if message.from_user is None:
        return
    body = (message.text or "").strip()
    if not body:
        return
    ok, err = validate_anonymous_content(body)
    if not ok:
        await message.answer(err or "Matn qabul qilinmadi.")
        return
    data = await state.get_data()
    tid = int(data.get("lc_reply_thread") or 0)
    qid = int(data.get("lc_reply_to") or 0)
    if tid < 1 or qid < 1:
        await state.clear()
        return
    pair = await crm.get_thread_with_listing(tid)
    if pair is None:
        await state.clear()
        return
    th, sub = pair
    if int(sub.user_telegram_id) != message.from_user.id:
        await state.clear()
        return
    await crm.add_listing_thread_message(
        tid,
        is_from_seller=True,
        body_text=body[:4000],
        voice_file_id=None,
        in_reply_to=qid,
    )
    buyer = int(th.buyer_telegram_id)
    try:
        await message.bot.send_message(
            buyer,
            "📩 <b>E’lon egasi javobi</b>\n\n" + html.escape(body[:3500]),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as e:
        logger.warning("Xaridorga javob yuborilmadi: %s", e)
        await _answer_with_root_menu(
            message,
            "Xabar yuborilmadi (foydalanuvchi botni bloklagan bo‘lishi mumkin).\n\n"
            "🏠 <b>Asosiy menyu</b> — davom etish uchun pastdagi tugmalardan foydalaning:",
        )
    else:
        await _answer_with_root_menu(
            message,
            "✅ <b>Javob yuborildi.</b>\n\n"
            "🏠 <b>Asosiy menyu</b> — davom etish uchun pastdagi tugmalardan foydalaning:",
        )
    await state.clear()


@router.message(StateFilter(ListingChatStates.seller_reply), F.voice)
async def lc_seller_voice(message: Message, state: FSMContext, crm: CrmRepository) -> None:
    if message.from_user is None or message.voice is None:
        return
    cap = message.caption
    if cap and cap.strip():
        ok, err = validate_anonymous_content(cap)
        if not ok:
            await message.answer(err or "Ovoz izohi qabul qilinmadi.")
            return
    data = await state.get_data()
    tid = int(data.get("lc_reply_thread") or 0)
    qid = int(data.get("lc_reply_to") or 0)
    if tid < 1 or qid < 1:
        await state.clear()
        return
    pair = await crm.get_thread_with_listing(tid)
    if pair is None:
        await state.clear()
        return
    th, sub = pair
    if int(sub.user_telegram_id) != message.from_user.id:
        await state.clear()
        return
    fid = message.voice.file_id
    await crm.add_listing_thread_message(
        tid,
        is_from_seller=True,
        body_text=cap.strip()[:500] if cap and cap.strip() else None,
        voice_file_id=fid,
        in_reply_to=qid,
    )
    buyer = int(th.buyer_telegram_id)
    try:
        await message.bot.send_message(
            buyer,
            "📩 <b>E’lon egasi javobi</b> — ovoz:",
            parse_mode=ParseMode.HTML,
        )
        await message.bot.send_voice(buyer, voice=fid)
    except TelegramBadRequest as e:
        logger.warning("Xaridorga ovoz yuborilmadi: %s", e)
        await _answer_with_root_menu(
            message,
            "Ovoz yuborilmadi.\n\n"
            "🏠 <b>Asosiy menyu</b> — davom etish uchun pastdagi tugmalardan foydalaning:",
        )
    else:
        await _answer_with_root_menu(
            message,
            "✅ <b>Ovozli javob yuborildi.</b>\n\n"
            "🏠 <b>Asosiy menyu</b> — davom etish uchun pastdagi tugmalardan foydalaning:",
        )
    await state.clear()


@router.message(StateFilter(ListingChatStates.seller_reply))
async def lc_seller_fallback(message: Message) -> None:
    await message.answer("Faqat matn yoki ovoz yuboring (yoki /start).")


@router.message(StateFilter(ListingChatStates.buyer_in_thread))
async def lc_buyer_fallback(message: Message) -> None:
    await message.answer(
        "💬 <b>Savol yozish</b> rejimidasiz.\n"
        "Faqat <b>matn</b> yoki <b>ovoz</b> yuboring (rasm/video emas).\n"
        "Tugatish: «Tugatdim» tugmasi.",
        parse_mode=ParseMode.HTML,
    )
