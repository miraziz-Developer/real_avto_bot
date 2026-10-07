"""Salon xizmatlari — AI (savdo agenti va kommentlar) ularni aniq bilishi uchun bitta joyda.

Real Avto faqat mashina sotmaydi: odamlardan mashina sotib oladi (vikup) va ularning e'lonini kanalga chiqaradi.
AI «biz faqat sotamiz» deb mijozni yo'qotmasligi kerak.
"""

from __future__ import annotations

from bot.config import settings


def sell_link(bot_username: str | None) -> str | None:
    """Mashinasini sotmoqchi bo'lgan odam uchun: botda e'lon berish oqimi (deep link)."""
    return f"https://t.me/{bot_username}?start=sell" if bot_username else None


def services_block(bot_username: str | None) -> str:
    link = sell_link(bot_username)
    where = f"botda «📢 E'lon berish» ({link})" if link else "botdagi «📢 E'lon berish» tugmasi orqali"
    fee = (
        f"e'lon narxi {settings.listing_price_uzs:,} so'm".replace(",", " ")
        if settings.listing_payment_enabled
        else "e'lon berish bepul"
    )
    return (
        "XIZMATLARIMIZ (hammasini bilib qo'y — «faqat sotamiz» dema):\n"
        "1. Mashina SOTAMIZ — kanaldagi/bazadagi sotuvdagi mashinalar.\n"
        "2. Mashinangizni SOTIB OLAMIZ (vikup) — kimdir o'z mashinasini sotmoqchi bo'lsa, biz sotib olishimiz mumkin.\n"
        f"3. Kanalga E'LON BERISH — o'z mashinasini kanalimizda sotish ({fee}).\n"
        f"Mashinasini sotmoqchi bo'lgan odamga (2 va 3): {where} — bir necha qadamda mashina ma'lumoti va rasmlarini "
        "yuboradi; jamoa ko'rib chiqadi: yoki o'zimiz narx taklif qilamiz, yoki e'loni kanalga chiqadi. "
        "Narxni oldindan aytma — mashinani ko'rmasdan baholab bo'lmaydi; menejer bilan ham bog'lanishi mumkin."
    )
