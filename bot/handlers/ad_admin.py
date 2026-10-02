"""E'lon moderatsiyasi: admin tasdiq/rad, kanalga post."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from aiogram.exceptions import TelegramBadRequest

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import ListingSubmissionStatus
from bot.db.repositories import CrmRepository
from bot.services.listing_publish import PublishError, publish_listing

router = Router(name="ad_admin")


class AdAdminRejectStates(StatesGroup):
    waiting_reason = State()


def _is_admin(uid: int | None) -> bool:
    if uid is None:
        return False
    return uid in settings.admin_telegram_ids


@router.callback_query(F.data.startswith("lad_a:"))
async def listing_approve(cq: CallbackQuery, crm: CrmRepository, cars: CarRepository) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    if cq.message is None:
        await cq.answer()
        return

    raw = (cq.data or "").split(":", 1)
    if len(raw) < 2 or not raw[1].isdigit():
        await cq.answer("Noto'g'ri tugma", show_alert=True)
        return
    lid = int(raw[1])

    sub = await crm.get_listing_submission(lid)
    if sub is None or sub.status != ListingSubmissionStatus.PENDING:
        await cq.answer("Bu e'lon allaqachon qayta ishlangan", show_alert=True)
        return
    if not sub.photo_file_ids:
        await cq.answer("Rasmlar yo'q", show_alert=True)
        return

    # Telegram spinner: answerCallbackQuery bitta marta; kanalga yuborishdan OLDIN yopamiz.
    await cq.answer()
    try:
        msgs = await publish_listing(cq.bot, crm, cars, sub)
    except PublishError as e:
        try:
            await cq.message.reply(e.html_text, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            pass
        return
    if msgs is None:
        try:
            await cq.message.reply("⚠️ Boshqa admin allaqachon tasdiqlagan.")
        except TelegramBadRequest:
            pass
        return
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    try:
        await cq.message.reply(f"✅ E'lon #{lid} kanalga joylandi.")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("lad_r:"))
async def listing_reject_start(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    if cq.message is None:
        await cq.answer()
        return

    raw = (cq.data or "").split(":", 1)
    if len(raw) < 2 or not raw[1].isdigit():
        await cq.answer("Noto'g'ri tugma", show_alert=True)
        return
    lid = int(raw[1])
    sub = await crm.get_listing_submission(lid)
    if sub is None or sub.status != ListingSubmissionStatus.PENDING:
        await cq.answer("Bu e'lon allaqachon qayta ishlangan", show_alert=True)
        return

    await cq.answer()

    await state.set_state(AdAdminRejectStates.waiting_reason)
    await state.update_data(
        reject_listing_id=lid,
        admin_chat_id=cq.message.chat.id,
        admin_message_id=cq.message.message_id,
    )
    await cq.message.answer(
        f"❌ <b>#{lid}</b> uchun <b>rad sababini</b> matn bilan yozing (kamida 4 belgi).\n"
        f"Bekor qilish: <code>/cancel</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("cancel"), StateFilter(AdAdminRejectStates.waiting_reason))
async def listing_reject_cancel(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        await state.clear()
        return
    await state.clear()
    await message.answer(
        "Rad etish bekor qilindi. E'lon moderatsiya navbatida qoldi.",
        parse_mode=ParseMode.HTML,
    )


@router.message(
    StateFilter(AdAdminRejectStates.waiting_reason),
    F.text,
    ~F.text.startswith("/"),
)
async def listing_reject_reason(message: Message, state: FSMContext, crm: CrmRepository) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        await state.clear()
        return

    reason = (message.text or "").strip()
    if len(reason) < 4:
        await message.answer("Sabab kamida 4 belgi bo'lsin.")
        return

    data = await state.get_data()
    lid = int(data.get("reject_listing_id") or 0)
    chat_id = data.get("admin_chat_id")
    msg_id = data.get("admin_message_id")
    await state.clear()

    if not lid:
        await message.answer("Sessiya buzildi. Qayta «Rad etish» tugmasidan foydalaning.")
        return

    updated = await crm.try_mark_listing_rejected(lid, reason=reason)
    if updated is None:
        await message.answer("Bu e'lon endi mavjud emas yoki boshqa admin hal qilgan.")
        return

    if isinstance(chat_id, int) and isinstance(msg_id, int):
        try:
            await message.bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
        except TelegramBadRequest:
            pass

    user_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Qayta e'lon yuborish", callback_data="ad_start")],
            [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")],
        ]
    )
    user_text = (
        "❌ <b>E'loningiz rad etildi.</b>\n\n"
        f"<b>Admin sababi:</b>\n{html.escape(reason)}\n\n"
        "Tuzatib qayta yuborishingiz mumkin — pastdagi tugma orqali yangi e'lon boshlang."
    )
    try:
        await message.bot.send_message(
            updated.user_telegram_id,
            user_text,
            parse_mode=ParseMode.HTML,
            reply_markup=user_kb,
        )
    except TelegramBadRequest as e:
        logging.warning("Rad xabari foydalanuvchiga yuborilmadi #%s: %s", lid, e)
        await message.answer(
            f"⚠️ #{lid} bazada rad etildi, lekin foydalanuvchiga xabar yuborilmadi "
            f"(bot bloklangan yoki chat yo'q): <code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(f"✅ #{lid} rad etildi; foydalanuvchiga sabab va tugmalar yuborildi.")


@router.message(StateFilter(AdAdminRejectStates.waiting_reason))
async def listing_reject_need_text(message: Message) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    await message.answer(
        "Rad sababini oddiy <b>matn</b> bilan yozing (kamida 4 belgi). "
        "Buyruqlar emas. Bekor: /cancel",
        parse_mode=ParseMode.HTML,
    )
