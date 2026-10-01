"""Admin tomoni: lead kartasi tugmalari va admin reply → mijozga javob (relay)."""

from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.models import Lead, LeadStatus
from bot.services.lead_cards import HOT_SCORE, LEAD_STATUS_LABELS, lead_admin_kb, lead_card_html, send_lead_card

logger = logging.getLogger(__name__)

router = Router(name="lead_admin")


def _is_admin(uid: int | None) -> bool:
    return uid is not None and uid in settings.admin_telegram_ids


async def _refresh_card(cq: CallbackQuery, lead: Lead, leads: LeadRepository, cars: CarRepository) -> None:
    msg = cq.message
    if msg is None or not hasattr(msg, "edit_text"):
        return
    car = await cars.get(lead.car_id) if lead.car_id else None
    text = lead_card_html(lead, car=car, last_messages=await leads.history(lead, limit=6))
    kb = lead_admin_kb(lead)
    try:
        if msg.photo:
            if len(text) <= 1024:
                await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await msg.edit_reply_markup(reply_markup=kb)
        else:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("Lead kartasini yangilab bo'lmadi: %s", e)


async def _notify_customer(bot: Bot, lead: Lead, text: str) -> None:
    try:
        await bot.send_message(lead.telegram_id, text, parse_mode=None)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("Lead #%s mijoziga xabar yuborilmadi: %s", lead.id, e)


@router.callback_query(F.data.startswith("lead:"))
async def lead_action(cq: CallbackQuery, bot: Bot, leads: LeadRepository, cars: CarRepository) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await cq.answer("Noto'g'ri tugma", show_alert=True)
        return
    action, lead_id = parts[1], int(parts[2])
    lead = await leads.get(lead_id)
    if lead is None:
        await cq.answer("Lead topilmadi", show_alert=True)
        return

    if action == "hist":
        await cq.answer()
        rows = await leads.history(lead, limit=60)
        lines = [f"💬 <b>Lead #{lead.id} suhbati</b> — {html.escape(lead.name or 'Mijoz')}", ""]
        for m in rows:
            who = {"user": "👤", "assistant": "🤖", "admin": "🧑‍💼"}.get(m.role, "•")
            lines.append(f"{who} <i>{m.created_at:%d.%m %H:%M}</i> {html.escape(m.content[:600])}")
        text = "\n".join(lines)
        if cq.message:
            for i in range(0, len(text), 4000):
                m = await cq.message.answer(text[i : i + 4000], parse_mode=ParseMode.HTML)
                await leads.add_relay(lead, m.chat.id, m.message_id)
        return

    admin_name = cq.from_user.first_name or "Menejer"
    if action == "take":
        if not await leads.take(lead, cq.from_user.id):
            await cq.answer("Bu mijozni boshqa menejer oldi", show_alert=True)
            return
        await leads.session.commit()
        await cq.answer("✅ Mijoz sizda. Reply orqali yozing — AI jim turadi.", show_alert=True)
        await _notify_customer(
            bot, lead, f"👤 Menejerimiz {admin_name} suhbatga qo'shildi. Savollaringizga endi u javob beradi."
        )
    elif action == "ai":
        await leads.back_to_ai(lead)
        await leads.session.commit()
        await cq.answer("🤖 Suhbat yana AI'da")
    elif action in ("won", "lost"):
        await leads.close(lead, LeadStatus.WON if action == "won" else LeadStatus.LOST)
        await leads.session.commit()
        await cq.answer(LEAD_STATUS_LABELS[lead.status])
        if action == "won" and lead.car_id and cq.message:
            await cq.message.answer(
                f"🏁 Tabriklaymiz! Mashinani ham sotildi deb belgilash: <code>/sotildi {lead.car_id}</code>",
                parse_mode=ParseMode.HTML,
            )
    else:
        await cq.answer("Noma'lum amal", show_alert=True)
        return
    await _refresh_card(cq, lead, leads, cars)


@router.message(F.chat.type == "private", F.reply_to_message)
async def admin_reply_relay(message: Message, bot: Bot, leads: LeadRepository) -> None:
    """Admin lead kartasi yoki mijoz xabariga reply qilsa — javob mijozga yuboriladi."""
    if message.from_user is None or not _is_admin(message.from_user.id) or message.reply_to_message is None:
        raise SkipHandler()
    if (message.text or "").startswith("/"):
        raise SkipHandler()
    lead = await leads.lead_by_relay(message.chat.id, message.reply_to_message.message_id)
    if lead is None:
        raise SkipHandler()
    try:
        await bot.copy_message(lead.telegram_id, from_chat_id=message.chat.id, message_id=message.message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await message.reply(f"❌ Mijozga yuborilmadi: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
        return
    await leads.add_message(lead, "admin", message.text or message.caption or "[media]")
    # Admin o'zi yozishni boshladi — AI endi bu suhbatga aralashmaydi
    if not lead.human_mode:
        await leads.take(lead, message.from_user.id)
    await leads.session.commit()
    await message.reply("✅ Mijozga yuborildi", parse_mode=None)


@router.message(F.chat.type == "private", Command("leadlar"))
async def cmd_open_leads(message: Message, leads: LeadRepository) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    rows = await leads.list_open(limit=30)
    if not rows:
        await message.answer("Ochiq mijozlar yo'q.")
        return
    lines = [f"🔥 <b>Ochiq mijozlar</b> ({len(rows)} ta):", ""]
    for ld in rows:
        mark = "🔥" if ld.score >= HOT_SCORE else "•"
        lines.append(
            f"{mark} <code>#{ld.id}</code> {html.escape(ld.name or 'Mijoz')} — "
            f"{LEAD_STATUS_LABELS.get(ld.status, ld.status)} · ball {ld.score}"
        )
    lines.append("")
    lines.append("Kartani ochish: <code>/lead ID</code>")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(F.chat.type == "private", Command("lead"))
async def cmd_lead(message: Message, command: CommandObject, bot: Bot, leads: LeadRepository, cars: CarRepository) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip().lstrip("#")
    lead = await leads.get(int(arg)) if arg.isdigit() else None
    if lead is None:
        await message.answer("Foydalanish: <code>/lead 12</code>", parse_mode=ParseMode.HTML)
        return
    car = await cars.get(lead.car_id) if lead.car_id else None
    await send_lead_card(bot, leads, lead, car=car, admin_ids=[message.from_user.id], header=f"📋 <b>Lead #{lead.id}</b>")
