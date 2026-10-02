"""AI kalitisiz (yoki AI ishlamay qolganda) oddiy javob: matndan model/byudjetni ajratib, bazadan ko'rsatish."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.agent.tools import AgentContext
from bot.config import settings
from bot.services.car_parser import parse_car_text

HANDOFF_CB = "agent:handoff"


def fallback_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📞 Menejer bilan bog'lanish", callback_data=HANDOFF_CB)],
            [InlineKeyboardButton(text="🔔 Chiqsa xabar ber (qidiruv saqlash)", callback_data="wishlist_start")],
        ]
    )


def _fmt_usd(n: int | None) -> str:
    return f"${n:,}".replace(",", " ") if n else "narx so'rang"


async def fallback_reply(ctx: AgentContext, text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    reply, kb = await _fallback_reply(ctx, text)
    if not ctx.buttons_supported:
        # Business chat / Instagram'da tugmalar yo'q — menejer suhbatni ko'rib turadi, o'zi javob beradi
        reply = reply.replace(" 👇", "").rstrip() + "\nMenejerimiz ham tez orada javob beradi."
        return reply, None
    return reply, kb


async def _fallback_reply(ctx: AgentContext, text: str) -> tuple[str, InlineKeyboardMarkup]:
    p = parse_car_text(text, usd_rate_uzs=settings.usd_rate_uzs)
    if not (p.brand or p.model or p.price_usd):
        return (
            "Assalomu alaykum! Qanday mashina qidiryapsiz? Model va byudjetni yozing, masalan: "
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
        lines.append(f"• {c.title} — {_fmt_usd(c.price_usd)}{km}{reserved}")
    lines.append("")
    lines.append("Ko'rishga kelish yoki batafsil ma'lumot uchun menejer bilan bog'laning 👇")
    return "\n".join(lines), fallback_kb()
