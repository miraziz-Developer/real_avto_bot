"""Tasdiqlangan e'lon uchun wishlist mosligi va foydalanuvchiga xabar."""

from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import ListingSubmission, Wishlist
from bot.db.repositories import CrmRepository
from bot.utils.contact_html import sales_phones_links_html
from bot.utils.currency import fmt_usd

logger = logging.getLogger(__name__)

_COND_LABEL = {
    "ideal": "Ideal",
    "yaxshi": "Yaxshi",
    "qoniqarli": "Qoniqarli",
    "tamir": "Ta'mir kerak",
}


def _cond_label(key: str) -> str:
    return _COND_LABEL.get((key or "").lower().strip(), key or "—")


def _format_match_message(sub: ListingSubmission, wish: Wishlist) -> str:
    price_txt = fmt_usd(sub.price_ask_usd)
    if wish.budget_min:
        bud = f"Siz qidirgan byudjet: <b>{fmt_usd(wish.budget_min)}</b> — <b>{fmt_usd(wish.budget_max)}</b>"
    else:
        bud = f"Siz qidirgan byudjet: <b>{fmt_usd(wish.budget_max)}</b> gacha"
    dtp = "Yo'q" if not sub.has_accident else "Ha"
    extra = ""
    if sub.price_ask_usd <= wish.budget_max:
        diff = wish.budget_max - sub.price_ask_usd
        if diff > 0:
            extra = f"\n\nBu sizning maksimal byudjetingizdan taxminan <b>{fmt_usd(diff)}</b> arzon."
    pl = sales_phones_links_html()
    return (
        "🎉 <b>Siz qidirgan mashina topildi!</b>\n\n"
        f"🚗 {html.escape(sub.brand)} {html.escape(sub.model)} — <b>{sub.year}</b> yil\n"
        f"🛣 Yurish: <b>{sub.mileage:,}</b> km\n"
        f"✨ Holat: {_cond_label(sub.condition_key)} | DTP: {dtp}\n"
        f"💰 Narx: <code>{html.escape(price_txt)}</code>\n"
        f"<i>{bud}</i>"
        f"{extra}\n\n"
        "📞 <b>Savdo va mashina haqida</b>:\n"
        f"{pl}\n\n"
        "Pastdan e’lonni oching yoki «Savol yozish» tugmasidan foydalaning."
    )


async def notify_wishlist_matches(bot: Bot, crm: CrmRepository, sub: ListingSubmission) -> None:
    """Mos wishlist egalariga xabar. Qidiruv faol qoladi — keyingi mos e'lonlar uchun ham xabar ketadi."""
    try:
        pairs = await crm.find_wishlists_matching_listing(sub)
        logger.info(
            "Wishlist moslash e'lon #%s (%s %s): %d ta mijoz",
            sub.id,
            (sub.brand or "").strip(),
            (sub.model or "").strip(),
            len(pairs),
        )
    except Exception:
        logger.exception("wishlist moslash topilmadi (listing #%s)", sub.id)
        return

    lid = sub.id
    try:
        me = await bot.get_me()
        bun = (me.username or "").strip().lstrip("@")
        ask_url = f"https://t.me/{bun}?start=lq_{lid}" if bun else None
        for wish, client in pairs:
            if client.telegram_id is None:
                continue
            text = _format_match_message(sub, wish)
            rows = [
                [
                    InlineKeyboardButton(
                        text="✅ E’lonni ko‘rish",
                        callback_data=f"wl_y:{wish.id}:{lid}",
                    ),
                    InlineKeyboardButton(
                        text="⏭ Keyinroq",
                        callback_data=f"wl_l:{wish.id}",
                    ),
                ],
            ]
            if ask_url:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="❓ Mashina haqida savol",
                            url=ask_url,
                        ),
                    ],
                )
            kb = InlineKeyboardMarkup(inline_keyboard=rows)
            try:
                await bot.send_message(
                    int(client.telegram_id),
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                await crm.mark_wishlist_notified(wish)
            except TelegramBadRequest as e:
                logger.warning("Wishlist xabar yuborilmadi wish=%s tg=%s: %s", wish.id, client.telegram_id, e)
            except Exception:
                logger.exception("Wishlist xabar wish=%s", wish.id)
    except Exception:
        logger.exception("wishlist notify jarayoni listing #%s", lid)
