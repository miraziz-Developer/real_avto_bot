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
    InputMediaPhoto,
    Message,
)

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.config import settings
from bot.db.models import ListingSubmissionStatus
from bot.db.repositories import CrmRepository
from bot.handlers.ad_listing import (
    listing_caption_from_sub_public,
    send_admin_listing_album_with_actions,
    truncate_caption_html,
)
from bot.services.wishlist_notify import notify_wishlist_matches

router = Router(name="ad_admin")


class AdAdminRejectStates(StatesGroup):
    waiting_reason = State()


def _is_admin(uid: int | None) -> bool:
    if uid is None:
        return False
    return uid in settings.admin_telegram_ids


PENDING_RESEND_LIMIT = 10


def moderation_keyboard(lid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"lad_a:{lid}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"lad_r:{lid}"),
            ]
        ]
    )


@router.message(Command("stats"), F.chat.type == "private")
async def admin_stats(message: Message, crm: CrmRepository) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    st = await crm.admin_stats()
    await message.answer(
        "📊 <b>Real Avto — statistika</b>\n\n"
        f"👤 Bot foydalanuvchilari: <b>{st['users']}</b>\n"
        f"🗂 Mijozlar (CRM): <b>{st['clients']}</b>\n\n"
        f"📝 E'lonlar: kutilmoqda <b>{st['pending']}</b> · tasdiqlangan <b>{st['approved']}</b> · "
        f"rad etilgan <b>{st['rejected']}</b>\n"
        f"🕐 So'nggi 24 soat: yuborilgan <b>{st['submitted_24h']}</b> · tasdiqlangan <b>{st['approved_24h']}</b>\n"
        f"✅ Sotildi: <b>{st['sold']}</b>\n\n"
        f"🔎 Faol qidiruvlar: <b>{st['wishlists_active']}</b>\n"
        f"💬 Savol-javob suhbatlari: <b>{st['threads']}</b>\n\n"
        "Kutilayotgan e'lonlarni qayta ko'rish: /pending",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("pending"), F.chat.type == "private")
async def admin_pending(message: Message, crm: CrmRepository) -> None:
    """Moderatsiya xabari yo'qolgan / o'chirilgan bo'lsa — kutilayotgan e'lonlarni qayta yuborish."""
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    rows = await crm.list_pending_listings(limit=PENDING_RESEND_LIMIT)
    if not rows:
        await message.answer("✅ Moderatsiya navbati bo'sh.")
        return
    await message.answer(
        f"🛎 Kutilayotgan e'lonlar: <b>{len(rows)}</b>"
        + (f" (eng eski {PENDING_RESEND_LIMIT} tasi)" if len(rows) >= PENDING_RESEND_LIMIT else "")
        + ". Har biri pastda tugmalar bilan.",
        parse_mode=ParseMode.HTML,
    )
    for sub in rows:
        client = await crm.get_client_by_id(sub.client_id)
        photos = list(sub.photo_file_ids or [])
        if not photos:
            await message.answer(f"⚠️ #{sub.id}: rasmlar yo'q.", reply_markup=moderation_keyboard(sub.id))
            continue
        try:
            await send_admin_listing_album_with_actions(
                message.bot,
                message.chat.id,
                lid=sub.id,
                tg_id=int(sub.user_telegram_id),
                client_name=(client.full_name if client else None) or "Mijoz",
                brand=sub.brand,
                model=sub.model,
                year=sub.year,
                mileage=sub.mileage,
                location=sub.location,
                condition_key=sub.condition_key,
                has_accident=sub.has_accident,
                price_ask_usd=sub.price_ask_usd,
                paint_status=sub.paint_status,
                phone=sub.phone,
                extra_details=sub.extra_details or "",
                seller_username=sub.seller_username,
                photo_file_ids=photos,
                mod_kb=moderation_keyboard(sub.id),
                payment_screenshot_file_id=sub.payment_screenshot_file_id,
            )
        except TelegramBadRequest as e:
            logging.warning("/pending: #%s yuborilmadi: %s", sub.id, e)
            await message.answer(
                f"⚠️ #{sub.id} albomi yuborilmadi: <code>{html.escape(str(e))}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=moderation_keyboard(sub.id),
            )


@router.callback_query(F.data.startswith("lad_a:"))
async def listing_approve(cq: CallbackQuery, crm: CrmRepository) -> None:
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

    # Qatorni qulflaymiz: ikkinchi admin shu yerda kutadi va keyin «allaqachon qayta ishlangan» oladi —
    # e'lon kanalga ikki marta chiqmaydi.
    sub = await crm.lock_pending_listing(lid)
    if sub is None:
        await cq.answer("Bu e'lon allaqachon qayta ishlangan", show_alert=True)
        return

    caption = truncate_caption_html(listing_caption_from_sub_public(sub))
    photos = list(sub.photo_file_ids or [])
    if not photos:
        await cq.answer("Rasmlar yo'q", show_alert=True)
        return

    media = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode="HTML")]
    media.extend(InputMediaPhoto(media=p) for p in photos[1:])

    # Telegram spinner: answerCallbackQuery bitta marta; kanalga yuborishdan OLDIN yopamiz.
    await cq.answer()

    try:
        msgs = await cq.bot.send_media_group(settings.channel_id, media)
    except TelegramBadRequest as e:
        logging.exception("Kanalga e'lon yuborish: %s", e)
        err = html.escape(str(e))
        hint = ""
        if "chat not found" in str(e).lower():
            hint = (
                "\n\n<b>Nima qilish kerak</b>\n"
                "• <code>.env</code> dagi <code>CHANNEL_ID</code> — "
                "tasdiqlangan e'lonlar shu kanalga chiqadi (<code>-100…</code> yoki <code>@kanal</code>).\n"
                "• Botni shu kanalga qo‘shing va <b>Post messages</b> (yoki admin) huquqi bo‘lsin.\n"
                "• ID ni @RawDataBot / @getidsbot orqali kanaldan oling."
            )
        try:
            await cq.message.reply(
                f"❌ Kanal xatosi: {err}{hint}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass
        return

    first_id = msgs[0].message_id if msgs else None
    updated = await crm.try_mark_listing_approved(lid, channel_message_id=first_id)
    if updated is None:
        for m in msgs:
            try:
                await cq.bot.delete_message(settings.channel_id, m.message_id)
            except TelegramBadRequest:
                pass
        try:
            await cq.message.reply("⚠️ Boshqa admin allaqachon tasdiqlagan.")
        except TelegramBadRequest:
            pass
        return

    # Tasdiqni darhol saqlaymiz (qulf bo'shaydi); keyingi Telegram so'rovlari xato bersa ham
    # e'lon holati kanal bilan mos qoladi.
    await crm.session.commit()

    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    try:
        await cq.message.reply(f"✅ E'lon #{lid} kanalga joylandi.")
    except TelegramBadRequest:
        pass

    bot_un = ""
    try:
        bot_me = await cq.bot.get_me()
        bot_un = (bot_me.username or "").strip().lstrip("@")
    except Exception:
        logging.exception("get_me muvaffaqiyatsiz — kanal ostidagi anonim havola yuborilmaydi")

    if bot_un and first_id is not None:
        try:
            ask = f"https://t.me/{bot_un}?start=lq_{lid}"
            await cq.bot.send_message(
                settings.channel_id,
                "💬 <b>Mashina haqida savol</b> — bot orqali yozishingiz mumkin.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="💬 Savol yozish", url=ask)],
                    ],
                ),
                reply_to_message_id=first_id,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as e:
            logging.warning("Kanalga savol tugmasi yuborilmadi: %s", e)

    try:
        ask_dm = f"https://t.me/{bot_un}?start=lq_{lid}" if bot_un else ""
        extra = ""
        if ask_dm:
            extra = (
                '\n\nKanaldagi post ostida «<a href="'
                + html.escape(ask_dm)
                + '">Savol yozish</a>» tugmasi ham bor.'
            )
        await cq.bot.send_message(
            sub.user_telegram_id,
            "✅ E'loningiz tasdiqlandi va kanalda e'lon qilindi. Rahmat!" + extra,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        pass

    try:
        await notify_wishlist_matches(cq.bot, crm, updated)
    except Exception:
        logging.exception("Wishlist xabarnomalari (e'lon #%s)", lid)


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
    except (TelegramBadRequest, TelegramForbiddenError) as e:
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
