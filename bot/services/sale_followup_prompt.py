"""«Sotildi / hali sotilmadi» DM matni va tugmalari (fon worker)."""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import ListingSubmission


def sale_followup_prompt_markup(listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Sotildi", callback_data=f"lfs:{listing_id}:y"),
                InlineKeyboardButton(text="❌ Hali sotilmadi", callback_data=f"lfs:{listing_id}:n"),
            ],
        ],
    )


def sale_followup_prompt_html(sub: ListingSubmission, *, repeat_label: str) -> str:
    car = f"{sub.brand} {sub.model} ({sub.year})"
    return (
        f"🚗 <b>E'lon #{sub.id}</b>\n"
        f"{html.escape(car)}\n\n"
        "Kanaldagi e'lon bo'yicha: mashina <b>sotildimi</b>?\n"
        "Javobingiz bizga statistika va mijozlarga yordam beradi.\n\n"
        f"<i>{repeat_label.capitalize()} yana so‘raymiz — tugmalardan birini tanlang.</i>"
    )
