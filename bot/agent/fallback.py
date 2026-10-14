"""AI kalitisiz (yoki AI ishlamay qolganda) oddiy javob: matndan model/byudjetni ajratib, bazadan ko'rsatish."""

from __future__ import annotations

import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.agent.tools import SELLER_REASON, AgentContext
from bot.config import sales_phone_entries, settings
from bot.db.models import Car, CarStatus
from bot.services.car_parser import _PHONE_RE, normalize_text, parse_car_text
from bot.utils.currency import fmt_price

HANDOFF_CB = "agent:handoff"
NO_PRICE = "narx so'rang"


def fallback_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📞 Menejer bilan bog'lanish", callback_data=HANDOFF_CB)],
            [InlineKeyboardButton(text="🔔 Chiqsa xabar ber (qidiruv saqlash)", callback_data="wishlist_start")],
        ]
    )



async def fallback_reply(ctx: AgentContext, text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    reply, kb = await _fallback_reply(ctx, text)
    if not ctx.buttons_supported:
        # Business chat / Instagram'da tugmalar yo'q — menejer suhbatni ko'rib turadi, o'zi javob beradi
        reply = reply.replace(" 👇", "").rstrip() + "\nMenejerimiz ham tez orada javob beradi."
        return reply, None
    return reply, kb


# Mijoz o'z mashinasini sotmoqchi (vikup / e'lon) — bu mashina qidiruv emas
_SELL_INTENT_RE = re.compile(
    r"sotmoqchi|sotaman|sotib\s+olasiz|sotib\s+olasizlar|olasizlarmi|vikup|выкуп|e'?lon\s+ber|elon\s+ber|"
    r"продать|продаю|купите|объявлени",
    re.IGNORECASE,
)


def is_sell_intent(text: str) -> bool:
    return bool(_SELL_INTENT_RE.search(normalize_text(text)))


def sell_reply() -> tuple[str, InlineKeyboardMarkup]:
    phones = ", ".join(sales_phone_entries())
    return (
        "Ha, mashinangizni ko'rib chiqamiz 👍 Model, yil, probeg, narxi va telefon raqamingizni yozing — "
        f"menejerimiz o'zi qo'ng'iroq qiladi. Yoki o'zingiz qo'ng'iroq qiling: {phones}. "
        f"Mashinani ko'rsatish uchun manzilimizga ham olib kelishingiz mumkin: {settings.business_address}.",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📍 Manzil (xarita)", url=settings.map_url)]]),
    )


# Aniq mashina haqidagi savol («narxi qancha?», «probegi?», «hali bormi?»)
ABOUT_CAR_RE = re.compile(
    r"\?|narx|qancha|necha|probeg|yurgan|kraska|rang|holat|bormi|turibdimi|sotil|kredit|bo'lib|nasiya|"
    r"цена|сколько|пробег|краск|продан|ещ[её]",
    re.IGNORECASE,
)


def _car_line(c: Car) -> str:
    km = f", {c.mileage_km:,} km".replace(",", " ") if c.mileage_km else ""
    return f"{c.title} — {fmt_price(c.price_usd, empty=NO_PRICE)}{km}"


async def _about_car(ctx: AgentContext, car: Car) -> str:
    if car.status in (CarStatus.ACTIVE, CarStatus.RESERVED):
        facts = [_car_line(car)]
        extra = ", ".join(x for x in (car.color, car.transmission, car.fuel, car.paint_status) if x)
        if extra:
            facts.append(extra)
        if car.status == CarStatus.RESERVED:
            facts.append("Hozir bron qilingan — bron bekor bo'lsa xabar beramiz.")
        facts.append("Ko'rishga kelish yoki savollar uchun menejer bilan bog'laning 👇")
        return "\n".join(facts)
    similar = await ctx.cars.similar_offerable(car, limit=3)
    lines = [f"{car.title} afsuski sotilgan."]
    if similar:
        lines.append("Hozir sotuvda o'xshashlari bor:")
        lines += [f"• {_car_line(c)}" for c in similar]
    lines.append("Menejer boshqa variantlarni ham taklif qiladi 👇")
    return "\n".join(lines)


def _is_seller(ctx: AgentContext) -> bool:
    return (ctx.lead.wants or "").startswith("SOTADI") or (ctx.lead.handoff_reason or "").startswith(SELLER_REASON)


async def _seller_details(ctx: AgentContext, text: str) -> tuple[str, InlineKeyboardMarkup] | None:
    """Sotuvchi mashinasi/telefonini yozdi — saqlab, menejerlarga «qo'ng'iroq qiling» xabari (AI'siz)."""
    p = parse_car_text(text, usd_rate_uzs=settings.usd_rate_uzs)
    phone = _PHONE_RE.search(text)
    model = " ".join(str(x) for x in (p.brand, p.model) if x)
    if not model and (ctx.lead.wants or "").startswith("SOTADI: "):
        model = ctx.lead.wants.removeprefix("SOTADI: ").split(",")[0]
    if not (model or phone):
        return None
    args = {"model": model or "mashina", "year": p.year, "mileage_km": p.mileage_km, "price_usd": p.price_usd}
    if phone:
        args["phone"] = phone.group(0)
    await ctx.execute("save_seller_car", args)
    phones = ", ".join(sales_phone_entries())
    if not ctx.lead.phone:
        return "Rahmat! Telefon raqamingizni ham yozing — menejerimiz o'zi qo'ng'iroq qiladi.", sell_reply()[1]
    return (
        f"Rahmat, ma'lumot menejerga yuborildi — tez orada o'zi qo'ng'iroq qiladi. Xohlasangiz o'zingiz ham "
        f"qo'ng'iroq qiling: {phones}, yoki mashinani manzilimizga olib keling: {settings.business_address}.",
        sell_reply()[1],
    )


async def _fallback_reply(ctx: AgentContext, text: str) -> tuple[str, InlineKeyboardMarkup]:
    if is_sell_intent(text) or _is_seller(ctx):
        if not ctx.lead.wants or not ctx.lead.wants.startswith("SOTADI"):
            ctx.lead.wants = "SOTADI"
        done = await _seller_details(ctx, text)
        return done if done is not None else sell_reply()
    p = parse_car_text(text, usd_rate_uzs=settings.usd_rate_uzs)
    if not (p.brand or p.model or p.price_usd):
        car = await ctx.current_car() if ABOUT_CAR_RE.search(text) else None
        if car is not None:
            # Mijoz aniq mashina (post) haqida so'rayapti: «narxi qancha?», «probegi?»
            return await _about_car(ctx, car), fallback_kb()
        return (
            "Qanday mashina qidiryapsiz? Model va byudjetni yozing, masalan: "
            "«Cobalt 2020, 10 000$ gacha». Yoki menejer bilan bog'laning 👇",
            fallback_kb(),
        )
    rows = await ctx.cars.search_offerable(
        brand=p.brand,
        model=p.model,
        year_min=p.year,
        price_max_usd=p.price_usd,
        limit=3,
    )
    if p.model or p.brand:
        ctx.lead.wants = " ".join(str(x) for x in (p.brand, p.model, p.year) if x)
    if p.price_usd:
        ctx.lead.budget_usd = p.price_usd
    if not rows:
        return (
            "Hozir bunday mashina sotuvda yo'q. Chiqishi bilan xabar berishimiz uchun qidiruvni saqlang "
            "yoki menejer bilan bog'laning — u boshqa variantlarni taklif qiladi 👇",
            fallback_kb(),
        )
    if len(rows) == 1:
        ctx.lead.car_id = rows[0].id
    lines = ["Hozir sotuvda bor:"]
    for c in rows:
        km = f", {c.mileage_km:,} km".replace(",", " ") if c.mileage_km else ""
        reserved = " (bron)" if c.status == "reserved" else ""
        lines.append(f"• {c.title} — {fmt_price(c.price_usd, empty=NO_PRICE)}{km}{reserved}")
    lines.append("")
    lines.append("Ko'rishga kelish yoki batafsil ma'lumot uchun menejer bilan bog'laning 👇")
    return "\n".join(lines), fallback_kb()
