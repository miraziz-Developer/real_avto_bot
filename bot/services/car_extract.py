"""Post matnidan mashina ma'lumoti: avval qoidalar (car_parser), keyin AI bo'shliqlarni to'ldiradi."""

from __future__ import annotations

import logging
from datetime import datetime

from bot.ai import AIClient, AIError
from bot.services.car_parser import ParsedCar, parse_car_text

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM_PROMPT = """Sen O'zbekistondagi avtosalonning Telegram kanal postlarini tahlil qiluvchi yordamchisan.
Post o'zbek (lotin/kirill), rus yoki aralash tilda, slang bilan yozilgan bo'lishi mumkin
(masalan: "jentra" = Chevrolet Gentra, "kobalt" = Chevrolet Cobalt, "probeg" = yurgan masofa, "pozitsiya" = komplektatsiya).

Faqat postda YOZILGAN ma'lumotni ol. Topilmagan maydon uchun null qo'y. Hech narsani o'ylab topma.
"[Ovoz]:" bilan boshlangan qism — videodagi gapning avtomatik transkripsiyasi, unda xatolar bo'lishi mumkin.

NARX bilan ehtiyot bo'l: price_amount — faqat mashinaning TO'LIQ narxi.
Nasiya / bo'lib to'lash uchun boshlang'ich to'lov ("500 dollar pul bo'lsa nasiyaga beramiz", "oldindan 30%",
"boshlang'ich to'lov", "qolganini bo'lib to'laysiz") — bu narx EMAS: price_amount = null, buni notes ga yoz
(masalan: "Nasiyaga: boshlang'ich to'lov $500"). Shubha bo'lsa ham price_amount = null.

Faqat quyidagi JSON obyektni qaytar:
{
  "is_car_listing": true/false,        // bu post sotiladigan mashina e'lonimi (tabrik, reklama, umumiy post emas)
  "brand": string|null,                // lotin yozuvida: "Chevrolet", "Kia", "BYD", "JAC" ...
  "model": string|null,                // "Cobalt", "Gentra", "Nexia 3", "J7" ...
  "year": int|null,
  "mileage_km": int|null,              // "76.000km" -> 76000, "120 ming" -> 120000
  "price_amount": number|null,         // postdagi narx raqami
  "price_currency": "USD"|"UZS"|null,  // "$", "dollar" -> USD; "so'm", "mln" -> UZS
  "color": string|null,                // o'zbekcha: "oq", "qora", "kulrang", "mokriy asfalt" ...
  "transmission": "avtomat"|"mexanika"|null,
  "fuel": string|null,                 // "benzin", "metan", "propan", "elektr", "gibrid", "dizel"
  "position": string|null,             // masalan "2-pozitsiya"
  "paint_status": string|null,         // kraska holati qisqa: "toza", "1 eshik kraska" ...
  "has_accident": true/false/null,
  "location": string|null,
  "notes": string|null,                // holat, kamchilik va afzalliklar haqida 1-2 gapli qisqa xulosa, o'zbekcha
  "confidence": number                 // 0..1 — ma'lumotni qanchalik aniq ajratganing
}"""


def _as_int(v: object) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(float(str(v).replace(" ", "").replace(",", "")))
    except ValueError:
        return None


def _as_str(v: object, limit: int = 200) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s[:limit] if s and s.lower() not in {"null", "none", "-"} else None


def ai_dict_to_parsed(data: dict, *, usd_rate_uzs: int) -> ParsedCar:
    """AI JSON javobini tekshirib, ParsedCar ga aylantirish (noto'g'ri qiymatlar tashlab yuboriladi)."""
    year = _as_int(data.get("year"))
    if year is not None and not (1980 <= year <= datetime.now().year + 1):
        year = None
    km = _as_int(data.get("mileage_km"))
    if km is not None and not (0 <= km <= 2_000_000):
        km = None

    price_usd: int | None = None
    amount = data.get("price_amount")
    try:
        amount_f = float(amount) if amount is not None and not isinstance(amount, bool) else None
    except (TypeError, ValueError):
        amount_f = None
    if amount_f:
        cur = str(data.get("price_currency") or "").upper()
        if cur not in {"USD", "UZS"}:
            cur = "UZS" if amount_f >= 1_000_000 else "USD"
        usd = amount_f / max(1, usd_rate_uzs) if cur == "UZS" else amount_f
        if 300 <= usd <= 2_000_000:
            price_usd = int(round(usd))

    trans = _as_str(data.get("transmission"), 30)
    if trans and trans.lower() not in {"avtomat", "mexanika"}:
        trans = None
    acc = data.get("has_accident")
    conf = data.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(conf))) if conf is not None else None
    except (TypeError, ValueError):
        confidence = None
    is_listing = data.get("is_car_listing")

    return ParsedCar(
        brand=_as_str(data.get("brand"), 100),
        model=_as_str(data.get("model"), 100),
        year=year,
        mileage_km=km,
        price_usd=price_usd,
        color=_as_str(data.get("color"), 60),
        transmission=trans.lower() if trans else None,
        fuel=_as_str(data.get("fuel"), 30),
        position=_as_str(data.get("position"), 30),
        paint_status=_as_str(data.get("paint_status")),
        has_accident=acc if isinstance(acc, bool) else None,
        location=_as_str(data.get("location")),
        notes=_as_str(data.get("notes"), 600),
        is_car_listing=is_listing if isinstance(is_listing, bool) else None,
        confidence=confidence,
    )


async def extract_car(text: str, *, ai: AIClient | None, usd_rate_uzs: int) -> ParsedCar:
    parsed = parse_car_text(text, usd_rate_uzs=usd_rate_uzs)
    if ai is None or not ai.enabled or not text.strip():
        parsed.confidence = 0.9 if parsed.is_complete() else 0.4
        return parsed
    try:
        data = await ai.chat_json(EXTRACT_SYSTEM_PROMPT, text[:4000])
    except AIError as e:
        logger.warning("AI tahlil ishlamadi, faqat regex natijasi: %s", e)
        parsed.confidence = 0.9 if parsed.is_complete() else 0.4
        return parsed
    ai_parsed = ai_dict_to_parsed(data, usd_rate_uzs=usd_rate_uzs)
    parsed.merge_missing(ai_parsed)
    parsed.is_car_listing = ai_parsed.is_car_listing
    parsed.notes = ai_parsed.notes
    # Regex asosiy maydonlarni to'liq topgan bo'lsa, AI ishonchi past bo'lsa ham natija ishonchli
    ai_conf = ai_parsed.confidence if ai_parsed.confidence is not None else 0.5
    parsed.confidence = max(ai_conf, 0.9) if parsed.is_complete() else min(ai_conf, 0.6)
    parsed.extra = {"ai": data}
    return parsed
