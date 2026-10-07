"""Kanal post matnidan mashina ma'lumotini ajratish (AI'siz, qoidalar asosida).

Real postlar tartibsiz: «yili: 2023/2024», «probeg: 76.000km», «$9 500», «115 mln», «jentra», «кобальт».
Bu parser aniq belgilangan maydonlarni ishonchli oladi; AI (bot/ai) qolgan bo'shliqlarni to'ldiradi.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from bot.data.car_catalog import BRAND_MODELS

# --- Sotildi belgisi -------------------------------------------------------------

# «sotiladi» (sotuvda) ni «sotildi» deb adashtirmaslik uchun aniq so'zlar.
_SOLD_RE = re.compile(
    r"(?<![\w])(sotildi|sotilgan|сотилди|сотилган|продан[оа]?|sold\s*out|sold)(?![\w])",
    re.IGNORECASE,
)


# Keyingi tahrir/reply'dagina sotildi belgisi (yangi e'lon matnida «barakasini bersin» uchrashi mumkin)
_SOLD_EXTRA_RE = re.compile(
    r"(?<![\w'])(baraka\w*|olib\s+ketildi|olib\s+ketishdi|sotib\s+olindi|qo'ldan\s+ketdi|"
    r"барака\w*|забрали)(?![\w'])",
    re.IGNORECASE,
)
_RESERVED_RE = re.compile(r"(?<![\w])(bron|bronlandi|бронь|брон|zakalat|задаток)(?![\w])", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?998[\s\-]?)?\(?\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")


# «sotildi?» (savol), «sotilgan emas», «hali sotilmadi», «не продан» — sotildi EMAS
_AFTER_NEGATION_RE = re.compile(r"^\s*(\?|emas\b|yo'q\b|эмас|эмас\b|йўқ|нет\b|ли\b|mi\b|ми\b)", re.IGNORECASE)
_BEFORE_NEGATION_RE = re.compile(r"(\bне|\bhali|\bхали)\s*$", re.IGNORECASE)


def _affirmed(rx: re.Pattern, t: str) -> bool:
    """Kalit so'z savol yoki inkor bilan emas, tasdiq ma'nosida kelganmi."""
    for m in rx.finditer(t):
        after = t[m.end() : m.end() + 12]
        before = t[max(0, m.start() - 8) : m.start()]
        if _AFTER_NEGATION_RE.match(after) or _BEFORE_NEGATION_RE.search(before):
            continue
        return True
    return False


def is_sold_text(text: str | None, *, extended: bool = False) -> bool:
    """extended=True — tahrir/reply uchun: «baraka bo'ldi», «olib ketildi» ham sotildi hisoblanadi."""
    if not text:
        return False
    t = normalize_text(text)
    return _affirmed(_SOLD_RE, t) or (extended and _affirmed(_SOLD_EXTRA_RE, t))


# Sotuvdan keyingi minnatdorchilik / tabrik (ko'pincha mijoz bilan dumaloq video): «mashinangiz muborak»,
# «olib ketishdi», «sotib oldim». Yangi e'londa bunday so'zlar deyarli bo'lmaydi («baraka» bundan mustasno —
# u e'lon matnida ham uchraydi, shuning uchun bu ro'yxatga kirmaydi)
_SOLD_THANKS_RE = re.compile(
    r"(?<![\w'])(muborak\w*|tabrik\w*|olib\s+ket(?:ishdi|ildi|di|dilar|yapti\w*|ayapti\w*)|sotib\s+old\w*|"
    r"sotib\s+olindi|xarid\s+qild\w*|yangi\s+egasi\w*|topshirildi|муборак\w*|мубарак\w*|табрик\w*|"
    r"поздравля\w*|купил\w*|забрал\w*)(?![\w'])",
    re.IGNORECASE,
)


def is_sold_thanks(text: str | None) -> bool:
    """Sotib olingani uchun tabrik/minnatdorchilik (matn yoki video ovozi)."""
    return bool(text) and _affirmed(_SOLD_THANKS_RE, normalize_text(text))


def is_sold_confirmation(text: str | None) -> bool:
    """Postga reply / tahrir uchun: «sotildi», «baraka bo'ldi», «muborak», «olib ketishdi» — hammasi sotildi."""
    return is_sold_text(text, extended=True) or is_sold_thanks(text)


# «Bron qilish mumkin», «bron uchun yozing» — taklif, bron qilingan degani emas
_RESERVE_OFFER_RE = re.compile(r"^\s*(qilish\w*|qiling\b|qilinglar\b|qilsa\w*|qilmoq\w*|uchun|mumkin|qabul|olamiz|бронировать|для)\b", re.IGNORECASE)


def is_reserved_text(text: str | None) -> bool:
    if not text:
        return False
    t = normalize_text(text)
    for m in _RESERVED_RE.finditer(t):
        after = t[m.end() : m.end() + 15]
        if m.group(1).lower() in ("bron", "брон", "бронь") and _RESERVE_OFFER_RE.match(after):
            continue
        if _affirmed(_RESERVED_RE, t[m.start() : m.end() + 15]):
            return True
    return False


def has_phone(text: str | None) -> bool:
    return bool(text and _PHONE_RE.search(text))


# --- Matnni tozalash -------------------------------------------------------------

_APOSTROPHES = str.maketrans({"‘": "'", "’": "'", "ʻ": "'", "ʼ": "'", "`": "'"})


def normalize_text(text: str) -> str:
    return (text or "").translate(_APOSTROPHES)


# --- Marka / model ---------------------------------------------------------------

# Slang, kirill va ruscha yozuvlar → (marka, model). Kalitlar kichik harfda.
_MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "cobalt": ("Chevrolet", "Cobalt"),
    "kobalt": ("Chevrolet", "Cobalt"),
    "kobolt": ("Chevrolet", "Cobalt"),
    "кобальт": ("Chevrolet", "Cobalt"),
    "кобалт": ("Chevrolet", "Cobalt"),
    "gentra": ("Chevrolet", "Gentra"),
    "jentra": ("Chevrolet", "Gentra"),
    "djentra": ("Chevrolet", "Gentra"),
    "гентра": ("Chevrolet", "Gentra"),
    "жентра": ("Chevrolet", "Gentra"),
    "джентра": ("Chevrolet", "Gentra"),
    "nexia 3": ("Chevrolet", "Nexia 3"),
    "nexia3": ("Chevrolet", "Nexia 3"),
    "nexia 2": ("Daewoo", "Nexia 2"),
    "nexia 1": ("Daewoo", "Nexia 1"),
    "nexia": ("Chevrolet", "Nexia"),
    "neksiya": ("Chevrolet", "Nexia"),
    "nexiya": ("Chevrolet", "Nexia"),
    "neksia": ("Chevrolet", "Nexia"),
    "нексия": ("Chevrolet", "Nexia"),
    "matiz": ("Chevrolet", "Matiz"),
    "матиз": ("Chevrolet", "Matiz"),
    "spark": ("Chevrolet", "Spark"),
    "спарк": ("Chevrolet", "Spark"),
    "damas": ("Chevrolet", "Damas"),
    "дамас": ("Chevrolet", "Damas"),
    "labo": ("Chevrolet", "Labo"),
    "лабо": ("Chevrolet", "Labo"),
    "lacetti": ("Chevrolet", "Lacetti"),
    "lasetti": ("Chevrolet", "Lacetti"),
    "ласетти": ("Chevrolet", "Lacetti"),
    "лачетти": ("Chevrolet", "Lacetti"),
    "malibu": ("Chevrolet", "Malibu"),
    "малибу": ("Chevrolet", "Malibu"),
    "tracker": ("Chevrolet", "Tracker"),
    "trekker": ("Chevrolet", "Tracker"),
    "трекер": ("Chevrolet", "Tracker"),
    "captiva": ("Chevrolet", "Captiva"),
    "kaptiva": ("Chevrolet", "Captiva"),
    "каптива": ("Chevrolet", "Captiva"),
    "onix": ("Chevrolet", "Onix"),
    "оникс": ("Chevrolet", "Onix"),
    "monza": ("Chevrolet", "Monza"),
    "equinox": ("Chevrolet", "Equinox"),
    "cruze": ("Chevrolet", "Cruze"),
    "epica": ("Chevrolet", "Epica"),
    "orlando": ("Chevrolet", "Orlando"),
    "tico": ("Daewoo", "Tico"),
    "тико": ("Daewoo", "Tico"),
    "lanos": ("Daewoo", "Lanos"),
}

_BRAND_ALIASES: dict[str, str] = {
    "chevrolet": "Chevrolet",
    "шевроле": "Chevrolet",
    "ravon": "Ravon",
    "daewoo": "Daewoo",
    "lada": "Lada",
    "kia": "Kia",
    "hyundai": "Hyundai",
    "toyota": "Toyota",
    "nissan": "Nissan",
    "volkswagen": "Volkswagen",
    "mercedes": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
    "bmw": "BMW",
    "byd": "BYD",
    "chery": "Chery",
    "geely": "Geely",
    "haval": "Haval",
    "renault": "Renault",
    "skoda": "Skoda",
    "mazda": "Mazda",
    "jac": "JAC",
    "changan": "Changan",
    "jetour": "Jetour",
    "kaiyi": "Kaiyi",
    "tesla": "Tesla",
    "leapmotor": "Leapmotor",
    "zeekr": "Zeekr",
}


def _build_catalog_aliases() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for brand, models in BRAND_MODELS.items():
        if brand == "Boshqa":
            continue
        for m in models:
            # Qisqa model nomlari («3», «6», «R2») matnda tasodifan ko'p uchraydi — faqat marka orqali topiladi.
            if len(m) < 3:
                continue
            out.setdefault(m.lower(), (brand, m))
    return out


# O'zbek/rus qo'shimchalari: «Gentrangiz», «Kobaltingiz», «Damasni», «Malibuni», «кобальтингиз», «кобальта».
# Faqat to'liq qo'shimcha ro'yxati — «so'ngi» kabi tasodifiy so'zlar model bo'lib qolmasin
_NAME_SUFFIX = (
    r"(?:ngiz|ingiz|nginiz|imiz|ning|ni|ga|ka|da|dan|dagi|dek|day|lar\w*|im|si|mi|chi|ku|yam|ham|"
    r"нгиз|ингиз|нинг|ни|га|да|дан|даги|лар\w*|ми|чи|а|у|е|ом|ой|ы|и)?"
)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    body = r"\s*".join(re.escape(part) for part in alias.split())
    # Qisqa yoki raqamli nomlarga qo'shimcha qo'shmaymiz («R3», «nexia 3»)
    suffix = _NAME_SUFFIX if len(alias) >= 4 and not alias[-1].isdigit() else ""
    return re.compile(r"(?<![\w])" + body + suffix + r"(?![\w])", re.IGNORECASE)


# Uzunroq nomlar avval («nexia 3» → «nexia» dan oldin)
_MODEL_PATTERNS: list[tuple[re.Pattern[str], tuple[str, str]]] = [
    (_alias_pattern(a), v)
    for a, v in sorted({**_build_catalog_aliases(), **_MODEL_ALIASES}.items(), key=lambda kv: -len(kv[0]))
]
_BRAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_alias_pattern(a), v) for a, v in sorted(_BRAND_ALIASES.items(), key=lambda kv: -len(kv[0]))
]
_MODEL_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9][\w\-]*")


def detect_brand_model(text: str) -> tuple[str | None, str | None]:
    model_hit: tuple[int, str, str] | None = None
    for pat, (brand, model) in _MODEL_PATTERNS:
        m = pat.search(text)
        if m and (model_hit is None or m.start() < model_hit[0]):
            model_hit = (m.start(), brand, model)
    brand_hit: tuple[int, int, str] | None = None
    for pat, brand in _BRAND_PATTERNS:
        m = pat.search(text)
        if m and (brand_hit is None or m.start() < brand_hit[0]):
            brand_hit = (m.start(), m.end(), brand)

    if model_hit:
        _, brand, model = model_hit
        # Matnda boshqa marka aniq yozilgan bo'lsa (masalan «Ravon Gentra») — shuni olamiz
        if brand_hit and brand_hit[2] != brand and model in BRAND_MODELS.get(brand_hit[2], []):
            brand = brand_hit[2]
        return brand, model
    if brand_hit:
        # Marka bor, model yo'q: markadan keyingi so'z model («JAC J7», «BYD Song»)
        _, end, brand = brand_hit
        tok = _MODEL_TOKEN_RE.search(text, end)
        model = None
        if tok and tok.start() - end <= 2:
            cand = tok.group(0)
            if not re.fullmatch(r"\d{4,}", cand):  # yil yoki narx emas (BMW 318, Peugeot 406 — model)
                model = cand.upper() if len(cand) <= 3 else cand.capitalize()
        return brand, model
    return None, None


# --- Raqamlar ---------------------------------------------------------------------

_YEAR = r"(19[89]\d|20[0-4]\d)"
_YEAR_LABELED_RE = re.compile(
    r"(?:yili|yil|год|year|ishlab\s+chiqarilgan)\s*[:\-–]?\s*" + _YEAR + r"(?!\d)",
    re.IGNORECASE,
)
_YEAR_SUFFIX_RE = re.compile(r"(?<!\d)" + _YEAR + r"\s*-?\s*(?:yil|y\.|год|г\.)", re.IGNORECASE)
_YEAR_ANY_RE = re.compile(r"(?<![\d.,$])" + _YEAR + r"(?!\d|[.,]\d|\s*(?:\$|km|км|ming|минг))")

_NUM = r"(\d{1,3}(?:[ ., ]\d{3})+|\d+(?:[.,]\d+)?)"
_THOUSAND = r"(ming|минг|тыс\.?|k|к)"

_MILEAGE_LABELED_RE = re.compile(
    r"(?:probeg|пробег|прабег|yurgani|yurgan|yurishi|yurish|mileage)\s*[:\-–]?\s*" + _NUM + r"\s*" + _THOUSAND + r"?",
    re.IGNORECASE,
)
_MILEAGE_KM_RE = re.compile(_NUM + r"\s*" + _THOUSAND + r"?\s*(?:km|км)(?![\w])", re.IGNORECASE)

_PRICE_LABELED_RE = re.compile(
    r"(?:narxi|narx|нархи|нарх|цена|price)\s*[:\-–]?\s*(\$)?\s*" + _NUM
    + r"\s*(mln|million|млн|ming|минг|k|к|тыс\.?)?\s*(\$|usd|у\.е\.|dollar|доллар|so'm|сўм|сум|sum|uzs)?",
    re.IGNORECASE,
)
_PRICE_DOLLAR_BEFORE_RE = re.compile(r"\$\s*" + _NUM + r"\s*(k|к)?(?![\w])", re.IGNORECASE)
_PRICE_DOLLAR_AFTER_RE = re.compile(_NUM + r"\s*(k|к)?\s*(?:\$|usd|у\.е\.|dollar)", re.IGNORECASE)
_PRICE_UZS_RE = re.compile(
    _NUM + r"\s*(mln|million|млн)?\s*(?:so'm|сўм|сум|sum|uzs)(?![\w])|" + _NUM + r"\s*(mln|million|млн)(?![\w])",
    re.IGNORECASE,
)


def _to_number(raw: str) -> float | None:
    s = raw.replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", s):
        return float(re.sub(r"[ .,]", "", s))
    s = s.replace(",", ".").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def _apply_multiplier(value: float, mult: str | None) -> float:
    m = (mult or "").lower().rstrip(".")
    if m in {"mln", "million", "млн"}:
        return value * 1_000_000
    if m in {"ming", "минг", "k", "к", "тыс"}:
        return value * 1_000
    return value


def parse_year(text: str) -> int | None:
    max_year = datetime.now().year + 1
    for rx in (_YEAR_LABELED_RE, _YEAR_SUFFIX_RE, _YEAR_ANY_RE):
        m = rx.search(text)
        if m:
            y = int(m.group(1))
            if 1980 <= y <= max_year:
                return y
    return None


def parse_mileage(text: str) -> int | None:
    for rx in (_MILEAGE_LABELED_RE, _MILEAGE_KM_RE):
        m = rx.search(text)
        if not m:
            continue
        val = _to_number(m.group(1))
        if val is None:
            continue
        km = int(_apply_multiplier(val, m.group(2)))
        if 0 <= km <= 2_000_000:
            return km
    return None


def _usd(amount: float, currency: str, usd_rate_uzs: int) -> int | None:
    usd = amount / max(1, usd_rate_uzs) if currency == "UZS" else amount
    usd_i = int(round(usd))
    return usd_i if 300 <= usd_i <= 2_000_000 else None


def _guess_currency(amount: float, explicit: str | None) -> str:
    e = (explicit or "").lower()
    if e in {"$", "usd", "у.е.", "dollar", "доллар"}:
        return "USD"
    if e in {"so'm", "сўм", "сум", "sum", "uzs"}:
        return "UZS"
    # Belgisiz: millionlar — so'm, qolgani — dollar (bozorda narx odatda $ da aytiladi)
    return "UZS" if amount >= 1_000_000 else "USD"


def parse_price_usd(text: str, usd_rate_uzs: int) -> int | None:
    m = _PRICE_LABELED_RE.search(text)
    if m:
        val = _to_number(m.group(2))
        if val is not None:
            amount = _apply_multiplier(val, m.group(3))
            explicit = "$" if m.group(1) else m.group(4)
            if (m.group(3) or "").lower() in {"mln", "million", "млн"} and not explicit:
                explicit = "so'm"
            usd = _usd(amount, _guess_currency(amount, explicit), usd_rate_uzs)
            if usd:
                return usd
    for rx in (_PRICE_DOLLAR_BEFORE_RE, _PRICE_DOLLAR_AFTER_RE):
        m = rx.search(text)
        if m:
            val = _to_number(m.group(1))
            if val is not None:
                usd = _usd(_apply_multiplier(val, m.group(2)), "USD", usd_rate_uzs)
                if usd:
                    return usd
    m = _PRICE_UZS_RE.search(text)
    if m:
        raw, mult = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        val = _to_number(raw)
        if val is not None:
            usd = _usd(_apply_multiplier(val, mult), "UZS", usd_rate_uzs)
            if usd:
                return usd
    return None


# --- Qo'shimcha maydonlar ---------------------------------------------------------

_POSITION_RE = re.compile(r"(?<!\d)(\d)\s*[-–]?\s*(?:pozitsiya|pozitsa|позиция|позиц|poz)", re.IGNORECASE)
_POSITION_RE2 = re.compile(r"(?:pozitsiya|pozitsa|позиция|poz)\w*\s*[:\-–]?\s*(\d)(?!\d)", re.IGNORECASE)
_TRANSMISSION = (
    (re.compile(r"avtomat|автомат|akpp|акпп", re.IGNORECASE), "avtomat"),
    (re.compile(r"mexanika|механика|мкпп|mkpp", re.IGNORECASE), "mexanika"),
)
_FUEL = (
    (re.compile(r"elektr|электро|electric", re.IGNORECASE), "elektr"),
    (re.compile(r"gibrid|гибрид|hybrid", re.IGNORECASE), "gibrid"),
    (re.compile(r"metan|метан", re.IGNORECASE), "metan"),
    (re.compile(r"propan|пропан", re.IGNORECASE), "propan"),
    (re.compile(r"dizel|дизел", re.IGNORECASE), "dizel"),
)
_COLORS = {
    "oq": "oq", "белый": "oq", "white": "oq",
    "qora": "qora", "черный": "qora", "чёрный": "qora", "black": "qora",
    "kulrang": "kulrang", "серый": "kulrang",
    "mokriy asfalt": "mokriy asfalt", "мокрый асфальт": "mokriy asfalt",
    "kumush": "kumush", "серебристый": "kumush", "silver": "kumush",
    "qizil": "qizil", "красный": "qizil",
    "ko'k": "ko'k", "синий": "ko'k",
    "jigarrang": "jigarrang", "коричневый": "jigarrang",
    "bej": "bej", "бежевый": "bej",
    "delfin": "delfin",
}
_COLOR_RE = re.compile(
    r"(?<![\w'])(" + "|".join(re.escape(c) for c in sorted(_COLORS, key=len, reverse=True)) + r")(?![\w'])",
    re.IGNORECASE,
)
# So'z chegarasi bilan: «kraskalari bor ekan» → «lari bor ekan» bo'lib qolmasin
_PAINT_RE = re.compile(
    r"(?<![\w])(?:kraskasi|kraska|краскаси|краска|покраска)(?![\w'])\s*[:\-–]?\s*([^\n,;]{2,80})", re.IGNORECASE
)
_LOCATION_RE = re.compile(r"(?:📍|manzil|lokatsiya|адрес)\s*[:\-–]?\s*([^\n]{2,80})", re.IGNORECASE)
# 📍 ba'zi postlarda boshqa maydonlar oldidan ham qo'yiladi («📍 probeg: 76.000km») — bunday qatorlar lokatsiya emas
_NOT_LOCATION_RE = re.compile(r"probeg|пробег|yili|narx|цена|\d\s*(?:km|км)|\$", re.IGNORECASE)
_ACCIDENT_NO_RE = re.compile(r"(?:dtp|дтп|avariya)\w*\s*[:\-–]?\s*(?:yo'q|йўқ|нет|net|bo'lmagan)", re.IGNORECASE)
_ACCIDENT_YES_RE = re.compile(r"(?:dtp|дтп|avariya)\w*\s*[:\-–]?\s*(?:bor|ha|бор|да|bo'lgan)(?![\w'])", re.IGNORECASE)


# --- Natija -----------------------------------------------------------------------

# Narx majburiy emas: Real Avto postlarida narx ko'pincha yozilmaydi («narxini menejer aytadi»)
REQUIRED_FIELDS = ("brand", "model", "year")


@dataclass
class ParsedCar:
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    mileage_km: int | None = None
    price_usd: int | None = None
    color: str | None = None
    transmission: str | None = None
    fuel: str | None = None
    position: str | None = None
    paint_status: str | None = None
    has_accident: bool | None = None
    location: str | None = None
    notes: str | None = None
    is_car_listing: bool | None = None
    confidence: float | None = None
    extra: dict = field(default_factory=dict)

    def missing_required(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS if getattr(self, f) in (None, "")]

    def is_complete(self) -> bool:
        return not self.missing_required()

    def looks_like_car(self) -> bool:
        if self.is_car_listing is not None:
            return self.is_car_listing
        signals = sum(1 for v in (self.brand or self.model, self.year, self.mileage_km, self.price_usd) if v)
        return signals >= 2

    def merge_missing(self, other: ParsedCar) -> ParsedCar:
        """Bo'sh maydonlarni boshqa natijadan to'ldirish (regex aniq topganini AI ustidan yozmaydi)."""
        for k, v in asdict(other).items():
            if k == "extra":
                continue
            if getattr(self, k) in (None, "") and v not in (None, ""):
                setattr(self, k, v)
        return self

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {})}


def parse_car_text(text: str, *, usd_rate_uzs: int) -> ParsedCar:
    t = normalize_text(text)
    brand, model = detect_brand_model(t)
    pos = _POSITION_RE.search(t) or _POSITION_RE2.search(t)
    color_m = _COLOR_RE.search(t)
    paint_m = _PAINT_RE.search(t)
    loc_m = next((m for m in _LOCATION_RE.finditer(t) if not _NOT_LOCATION_RE.search(m.group(1))), None)
    has_accident = True if _ACCIDENT_YES_RE.search(t) else (False if _ACCIDENT_NO_RE.search(t) else None)
    return ParsedCar(
        brand=brand,
        model=model,
        year=parse_year(t),
        mileage_km=parse_mileage(t),
        price_usd=parse_price_usd(t, usd_rate_uzs),
        color=_COLORS.get(color_m.group(1).lower()) if color_m else None,
        transmission=next((v for rx, v in _TRANSMISSION if rx.search(t)), None),
        fuel=next((v for rx, v in _FUEL if rx.search(t)), None),
        position=f"{pos.group(1)}-pozitsiya" if pos else None,
        paint_status=paint_m.group(1).strip(" .,") if paint_m else None,
        has_accident=has_accident,
        location=loc_m.group(1).strip(" .,") if loc_m else None,
    )


# --- Admin tuzatishi («narx 9800», «yil: 2021», «xarid 8000») -------------------------

_ADMIN_KEYS: dict[str, str] = {
    "narx": "price_usd", "narxi": "price_usd", "price": "price_usd", "цена": "price_usd",
    "yil": "year", "yili": "year", "год": "year",
    "probeg": "mileage_km", "пробег": "mileage_km", "km": "mileage_km",
    "marka": "brand", "brand": "brand",
    "model": "model",
    "rang": "color", "rangi": "color", "цвет": "color",
    "pozitsiya": "position", "poz": "position",
    "kraska": "paint_status", "kraskasi": "paint_status",
    "dtp": "has_accident", "avariya": "has_accident",
    "lokatsiya": "location", "manzil": "location",
    "izoh": "notes",
    "karobka": "transmission", "uzatma": "transmission",
    "yoqilgi": "fuel", "yoqilg'i": "fuel",
    "xarid": "purchase_price_usd", "olingan": "purchase_price_usd",
    "xarajat": "expenses_usd",
    "sotuv": "sold_price_usd", "sotildi": "sold_price_usd",
}
_ADMIN_LINE_RE = re.compile(r"^\s*([\w'ʻ’]+)\s*[:=\-–]?\s*(.+?)\s*$")


def _admin_money(value: str, usd_rate_uzs: int) -> int | None:
    return parse_price_usd(f"narx {value}", usd_rate_uzs)


def parse_admin_edit(text: str, *, usd_rate_uzs: int) -> tuple[dict, list[str]]:
    """Admin yozgan tuzatishlarni maydonlarga aylantirish. (qiymatlar, tushunilmagan qatorlar)."""
    values: dict = {}
    bad: list[str] = []
    # «narx 9800, yil 2021» — vergul+bo'shliq ajratuvchi («76,000» raqamini buzmaslik uchun)
    for raw in re.split(r"\n|,\s+|;", normalize_text(text)):
        line = raw.strip()
        if not line:
            continue
        m = _ADMIN_LINE_RE.match(line)
        key = _ADMIN_KEYS.get(m.group(1).lower()) if m else None
        if not m or not key:
            bad.append(line)
            continue
        val = m.group(2).strip()
        parsed: object = None
        if key in {"price_usd", "purchase_price_usd", "expenses_usd", "sold_price_usd"}:
            parsed = _admin_money(val, usd_rate_uzs)
            if parsed is None and key == "expenses_usd" and re.fullmatch(r"\d{1,3}", val):
                parsed = int(val)  # kichik xarajat ($50) narx chegarasidan past bo'lishi mumkin
        elif key == "year":
            parsed = parse_year(f"yili {val}")
        elif key == "mileage_km":
            parsed = parse_mileage(f"probeg {val}")
        elif key == "has_accident":
            low = val.lower()
            parsed = True if low in {"bor", "ha", "bo'lgan"} else (False if low in {"yo'q", "yoq", "yo'qq"} else None)
        elif key == "position":
            d = re.search(r"\d", val)
            parsed = f"{d.group(0)}-pozitsiya" if d else val[:30]
        elif key == "transmission":
            low = val.lower()
            parsed = "avtomat" if "avto" in low else ("mexanika" if "mex" in low else None)
        else:
            parsed = val[:200]
        if parsed is None:
            bad.append(line)
            continue
        values[key] = parsed
        if key == "purchase_price_usd":
            values["is_own"] = True
    return values, bad
