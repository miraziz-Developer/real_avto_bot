"""Lead kartasi (admin uchun): tayyor mijoz haqida hammasi bir joyda + tugmalar + javob relay."""

from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.db.leads_repo import LeadRepository
from bot.db.models import AgentMessage, Car, Lead, LeadStatus
from bot.utils.currency import fmt_price

logger = logging.getLogger(__name__)

LEAD_STATUS_LABELS = {
    LeadStatus.ACTIVE: "🤖 AI gaplashmoqda",
    LeadStatus.HANDED_OFF: "🔔 Menejer kutilmoqda",
    LeadStatus.IN_PROGRESS: "👤 Menejer gaplashmoqda",
    LeadStatus.WON: "🏁 Sotuv bo'ldi",
    LeadStatus.LOST: "❌ Yopilgan",
}

HOT_SCORE = 60


def lead_score(lead: Lead) -> int:
    """Mijoz qanchalik «pishgan»: qiziqqan mashina, byudjet, to'lov usuli, kelish vaqti, telefon."""
    score = 0
    if lead.car_id:
        score += 25
    if lead.budget_usd:
        score += 10
    if lead.payment_method:
        score += 15
    if lead.visit_time:
        score += 30
    if lead.phone:
        score += 20
    return min(score, 100)



def lead_card_html(
    lead: Lead,
    *,
    car: Car | None = None,
    last_messages: list[AgentMessage] | None = None,
    header: str | None = None,
) -> str:
    e = html.escape
    score = lead_score(lead)
    if header is None:
        header = "🔥 <b>ISSIQ MIJOZ</b>" if score >= HOT_SCORE else "🔔 <b>Mijoz menejer bilan gaplashmoqchi</b>"
    lines = [header, ""]
    name = e(lead.name or "Mijoz")
    if lead.channel == "instagram":
        # Instagram mijozi: telegram_id ustunida IGSID — Telegram havolasi emas, Instagram profili
        url = f"https://instagram.com/{lead.username}" if lead.username else None
        who = f'👤 <a href="{e(url)}">{name}</a> · Instagram' if url else f"👤 {name} · Instagram"
    else:
        who = f'👤 <a href="tg://user?id={lead.telegram_id}">{name}</a>'
        if lead.username:
            who += f" · @{e(lead.username)}"
    if lead.phone:
        who += f" · <code>{e(lead.phone)}</code>"
    lines.append(who)
    if car is not None:
        lines.append(f"🚗 {e(car.title)} — {fmt_price(car.price_usd, empty='—')}  <code>#{car.id}</code>")
    if lead.wants and car is None:
        lines.append(f"🔎 Qidiryapti: {e(lead.wants[:200])}")
    money = []
    if lead.budget_usd:
        money.append(f"byudjet {fmt_price(lead.budget_usd, empty='—')}")
    if lead.payment_method:
        money.append(e(lead.payment_method))
    if money:
        lines.append("💰 " + " · ".join(money))
    if lead.visit_time:
        lines.append(f"📅 Ko'rishga: <b>{e(lead.visit_time)}</b>")
    if lead.handoff_reason:
        lines.append(f"❗ Sabab: {e(lead.handoff_reason)}")
    if lead.summary:
        lines.append(f"📝 {e(lead.summary[:500])}")
    if last_messages:
        lines.append("")
        lines.append("💬 <b>Oxirgi xabarlar:</b>")
        for m in last_messages[-4:]:
            who_ = {"user": "👤", "assistant": "🤖", "admin": "🧑‍💼"}.get(m.role, "•")
            lines.append(f"{who_} {e(m.content[:180])}")
    lines.append("")
    lines.append(f"<i>Lead #{lead.id} · {LEAD_STATUS_LABELS.get(lead.status, lead.status)} · ball {score}/100</i>")
    lines.append("<i>Mijozga yozish: shu xabarga reply qiling.</i>")
    text = "\n".join(lines)
    return text[:4000]


def lead_admin_kb(lead: Lead) -> InlineKeyboardMarkup:
    lid = lead.id
    rows: list[list[InlineKeyboardButton]] = []
    if lead.status in (LeadStatus.ACTIVE, LeadStatus.HANDED_OFF):
        rows.append([InlineKeyboardButton(text="✅ Oldim — o'zim gaplashaman", callback_data=f"lead:take:{lid}")])
    if lead.status == LeadStatus.IN_PROGRESS:
        rows.append([InlineKeyboardButton(text="🤖 AI davom etsin", callback_data=f"lead:ai:{lid}")])
    if lead.status in (LeadStatus.ACTIVE, LeadStatus.HANDED_OFF, LeadStatus.IN_PROGRESS):
        rows.append(
            [
                InlineKeyboardButton(text="🏁 Sotuv bo'ldi", callback_data=f"lead:won:{lid}"),
                InlineKeyboardButton(text="❌ Yopish", callback_data=f"lead:lost:{lid}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="💬 To'liq suhbat", callback_data=f"lead:hist:{lid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_lead_card(
    bot: Bot,
    leads: LeadRepository,
    lead: Lead,
    *,
    car: Car | None = None,
    header: str | None = None,
    admin_ids: list[int] | None = None,
) -> int:
    """Kartani adminlarga yuborish (relay yoziladi — admin reply qilsa mijozga boradi). Yuborilganlar soni."""
    lead.score = lead_score(lead)
    history = await leads.history(lead, limit=6)
    text = lead_card_html(lead, car=car, last_messages=history, header=header)
    kb = lead_admin_kb(lead)
    targets = admin_ids if admin_ids is not None else list(settings.admin_telegram_ids)
    if not targets:
        logger.warning("ADMIN_TELEGRAM_IDS bo'sh — lead #%s kartasi yuborilmadi", lead.id)
        return 0
    sent = 0
    photo = car.photo_file_ids[0] if car is not None and car.photo_file_ids else None
    for aid in targets:
        try:
            if photo and len(text) <= 1024:
                m = await bot.send_photo(aid, photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                m = await bot.send_message(aid, text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
            await leads.add_relay(lead, aid, m.message_id)
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError) as err:
            logger.warning("Admin %s ga lead #%s kartasi yuborilmadi: %s", aid, lead.id, err)
    return sent
