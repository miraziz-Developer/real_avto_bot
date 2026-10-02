import html
import os
import re
from dataclasses import dataclass
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _normalize_chat_id(raw: str) -> str:
    """Bo'shliq, qo'shtirnoq; ba'zan nusxa-qo'yishda keladigan axlatni olib tashlash."""
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        s = s[1:-1].strip()
    return s


def _req(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Atribut talab qilinadi: {name} (.env)")
    return _normalize_chat_id(v)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _admin_telegram_ids() -> frozenset[int]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", "").strip()
    if not raw:
        return frozenset()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return frozenset(ids)


_SALES_PHONE_DEFAULT = "+998 90 123 45 67"


def _sales_phone() -> str:
    """Wishlist / savdo: .env dagi matn (bir nechta raqam vergul/nuqta-vergul/| bilan)."""
    return (os.getenv("SALES_PHONE", "") or "").strip() or _SALES_PHONE_DEFAULT


def sales_phone_entries() -> list[str]:
    """SALES_PHONE ni ajratib ro‘yxat (kanal e‘lonida bir nechta raqam chiqishi uchun)."""
    raw = (os.getenv("SALES_PHONE", "") or "").strip()
    if not raw:
        return [_SALES_PHONE_DEFAULT]
    parts = re.split(r"\s*[,;|]\s*|\n+", raw)
    out = [p.strip() for p in parts if p.strip()]
    return out if out else [_SALES_PHONE_DEFAULT]


def _optional_url(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    return v or None


_REAL_AVTO_MAP_DEFAULT = "https://www.google.com/maps?q=41.148170,69.017201"


def _parking_location_text() -> str:
    """Sotilmadi javobida; to‘liq override: PARKING_LOCATION_TEXT. Havola: REAL_AVTO_MAP_URL."""
    raw = (os.getenv("PARKING_LOCATION_TEXT") or "").strip()
    if raw:
        return raw
    map_url = (os.getenv("REAL_AVTO_MAP_URL") or "").strip() or _REAL_AVTO_MAP_DEFAULT
    safe_href = html.escape(map_url, quote=True)
    return (
        "📍 <b>Real Avto — parking / mashinani ko‘rish</b>\n"
        "Kelib, shaxsan ko‘rishingiz yoki menejer bilan kelishuv qilishingiz mumkin.\n\n"
        "Koordinatalar: <code>41°08'53.4\"N 69°01'01.9\"E</code>\n"
        f'<a href="{safe_href}">Google Maps — joylashuv</a>'
    )


def _reviews_channel_id() -> str:
    """Sharhlar kanali; bo‘sh qoldirilsa — @real_avto_otzivlar (bot kanalda admin bo‘lishi kerak)."""
    v = (os.getenv("REVIEWS_CHANNEL_ID") or "").strip()
    if v:
        return _normalize_chat_id(v)
    return _normalize_chat_id("@real_avto_otzivlar")


def _log_level() -> str:
    return (os.getenv("LOG_LEVEL", "INFO") or "INFO").strip().upper()


def _bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _optional_positive_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    v = int(raw)
    return v if v > 0 else None


@dataclass(frozen=True)
class Settings:
    database_url: str
    bot_token: str
    channel_id: str
    channel_username: str
    instagram_username: str
    leaderboard_channel_id: str
    sales_phone: str
    prize_usd: int
    leaderboard_interval_days: int
    admin_telegram_ids: frozenset[int]
    usd_rate_uzs: int
    redis_url: str | None
    db_pool_size: int
    db_max_overflow: int
    db_pool_recycle: int
    db_pool_timeout: int
    log_level: str
    reviews_channel_id: str
    listing_payment_enabled: bool
    listing_price_uzs: int
    payment_card: str
    payment_card_holder: str
    groq_api_key: str
    groq_model: str
    groq_stt_model: str
    car_stale_days: int
    agent_enabled: bool
    agent_model: str
    business_name: str
    business_address: str
    business_hours: str
    map_url: str
    lead_reminder_minutes: int
    business_enabled: bool
    business_owner_pause_hours: int
    comments_enabled: bool
    listing_freeze_hours: int
    work_hour_start: int
    work_hour_end: int
    buyout_reply_hours: int
    catalog_url: str
    crm_url: str
    instagram_enabled: bool
    ig_access_token: str
    ig_app_secret: str
    ig_verify_token: str
    ig_account_id: str
    ig_webhook_port: int
    ig_graph_version: str
    sale_followup_interval_hours: int
    sale_followup_interval_minutes: int | None
    parking_location_text: str



settings = Settings(
    database_url=_req("DATABASE_URL"),
    bot_token=_req("BOT_TOKEN"),
    channel_id=_req("CHANNEL_ID"),
    channel_username=_req("CHANNEL_USERNAME"),
    instagram_username=_req("INSTAGRAM_USERNAME"),
    leaderboard_channel_id=_req("LEADERBOARD_CHANNEL_ID"),
    sales_phone=_sales_phone(),
    prize_usd=_int("PRIZE_USD", 50),
    leaderboard_interval_days=_int("LEADERBOARD_INTERVAL_DAYS", 3),
    admin_telegram_ids=_admin_telegram_ids(),
    usd_rate_uzs=_int("USD_RATE_UZS", 13000),
    redis_url=_optional_url("REDIS_URL"),
    db_pool_size=_int("DB_POOL_SIZE", 20),
    db_max_overflow=_int("DB_MAX_OVERFLOW", 40),
    db_pool_recycle=_int("DB_POOL_RECYCLE", 3600),
    db_pool_timeout=_int("DB_POOL_TIMEOUT", 30),
    log_level=_log_level(),
    reviews_channel_id=_reviews_channel_id(),
    # Hozircha e'lon bepul; pullik rejimga qaytish uchun LISTING_PAYMENT_ENABLED=true
    listing_payment_enabled=_bool("LISTING_PAYMENT_ENABLED", False),
    listing_price_uzs=_int("LISTING_PRICE_UZS", 50000),
    payment_card=(os.getenv("PAYMENT_CARD", "") or "").strip(),
    payment_card_holder=(os.getenv("PAYMENT_CARD_HOLDER", "") or "").strip(),
    # AI (kanal postlarini tahlil, ovoz/dumaloq video → matn). Kalit bo'lmasa faqat regex parser ishlaydi.
    groq_api_key=(os.getenv("GROQ_API_KEY", "") or "").strip(),
    groq_model=(os.getenv("GROQ_MODEL", "") or "llama-3.3-70b-versatile").strip(),
    groq_stt_model=(os.getenv("GROQ_STT_MODEL", "") or "whisper-large-v3").strip(),
    # Shuncha kundan beri sotuvda turgan mashina uchun adminlarga «hali sotuvdami?» so'rovi
    car_stale_days=_int("CAR_STALE_DAYS", 14),
    # AI savdo agenti: botga yozilgan savollarga mashinalar bazasidan javob beradi
    agent_enabled=_bool("AGENT_ENABLED", True),
    agent_model=(os.getenv("GROQ_AGENT_MODEL", "") or os.getenv("GROQ_MODEL", "") or "llama-3.3-70b-versatile").strip(),
    business_name=(os.getenv("BUSINESS_NAME", "") or "Real Avto").strip(),
    business_address=(os.getenv("BUSINESS_ADDRESS", "") or "Yangiyo'l").strip(),
    # Bo'sh bo'lsa agent ish vaqtini aytmaydi (o'ylab topmaslik uchun) — menejer aniqlaydi
    business_hours=(os.getenv("BUSINESS_HOURS", "") or "").strip(),
    map_url=(os.getenv("REAL_AVTO_MAP_URL", "") or _REAL_AVTO_MAP_DEFAULT).strip(),
    # Adminga topshirilgan lead shuncha daqiqada olinmasa — barcha adminlarga qayta eslatma
    lead_reminder_minutes=_int("LEAD_REMINDER_MINUTES", 5),
    # Telegram Business: agent akkaunt egasining shaxsiy chatlarida javob beradi
    business_enabled=_bool("BUSINESS_ENABLED", True),
    # Akkaunt egasi mijozga o'zi yozsa — AI shu mijoz bilan shuncha soat jim turadi
    business_owner_pause_hours=_int("BUSINESS_OWNER_PAUSE_HOURS", 6),
    # Kanal kommentlaridagi savollarga bazadan qisqa javob + botga havola
    comments_enabled=_bool("COMMENTS_ENABLED", True),
    # Bot orqali kelgan e'lon shuncha ISH soati muzlatiladi (jamoa sotib olishi mumkin), keyin avtomatik kanalga.
    # 0 — avtomatik joylash yo'q (faqat qo'lda tasdiqlash)
    listing_freeze_hours=_int("LISTING_FREEZE_HOURS", 6),
    # Ish vaqti (Toshkent): muzlatish faqat shu soatlarda «sanaladi», avtomatik joylash ham shu vaqtda
    work_hour_start=_int("WORK_HOUR_START", 9),
    work_hour_end=_int("WORK_HOUR_END", 21),
    # Sotuvchi sotib olish taklifiga shuncha soatda javob bermasa — e'lon avtomatik kanalga chiqadi
    buyout_reply_hours=_int("BUYOUT_REPLY_HOURS", 24),
    # Mashinalar katalogi (sayt + Telegram Mini App). Mini App uchun HTTPS bo'lishi shart
    catalog_url=(os.getenv("CATALOG_URL", "") or "").strip().rstrip("/"),
    # Admin panel (CRM) — adminlarga bot ichida Mini App bo'lib ochiladi (HTTPS shart)
    crm_url=(os.getenv("CRM_URL", "") or "").strip().rstrip("/"),
    # Instagram (Meta App Review'dan keyin): Direct va kommentlarga agent javobi, webhook orqali
    instagram_enabled=_bool("INSTAGRAM_ENABLED", False),
    ig_access_token=(os.getenv("IG_ACCESS_TOKEN", "") or "").strip(),
    ig_app_secret=(os.getenv("IG_APP_SECRET", "") or "").strip(),
    ig_verify_token=(os.getenv("IG_VERIFY_TOKEN", "") or "").strip(),
    # O'zimizning Instagram akkaunt ID si (o'z kommentlarimizga javob bermaslik uchun)
    ig_account_id=(os.getenv("IG_ACCOUNT_ID", "") or "").strip(),
    ig_webhook_port=_int("IG_WEBHOOK_PORT", 8081),
    ig_graph_version=(os.getenv("IG_GRAPH_VERSION", "") or "v21.0").strip(),
    sale_followup_interval_hours=_int("SALE_FOLLOWUP_INTERVAL_HOURS", 24),
    sale_followup_interval_minutes=_optional_positive_int("SALE_FOLLOWUP_INTERVAL_MINUTES"),
    parking_location_text=_parking_location_text(),
)


def sale_followup_interval_timedelta(s: Settings = settings) -> timedelta:
    if s.sale_followup_interval_minutes is not None:
        return timedelta(minutes=max(1, s.sale_followup_interval_minutes))
    return timedelta(hours=max(1, s.sale_followup_interval_hours))


def sale_followup_first_prompt_after(interval: timedelta, s: Settings = settings) -> timedelta:
    """Birinchi «sotildimi» DM uchun kutish. Takroriy tsikldan qisqa bo‘lishi mumkin."""
    raw = _optional_positive_int("SALE_FOLLOWUP_FIRST_AFTER_MINUTES")
    if raw is not None:
        return min(interval, timedelta(minutes=max(1, raw)))
    # Uzoq tsikl (≥2 soat): birinchi so‘rov ~1 soatdan keyin — aks holda foydalanuvchi 24 soat «hech narsa kemadi» deb qolardi.
    if interval >= timedelta(hours=2):
        return min(interval, timedelta(hours=1))
    return interval


def sale_followup_repeat_label(s: Settings = settings) -> str:
    if s.sale_followup_interval_minutes is not None:
        m = max(1, s.sale_followup_interval_minutes)
        return f"har {m} daqiqada"
    h = max(1, s.sale_followup_interval_hours)
    return f"har {h} soatda"


def sale_followup_retry_hint(s: Settings = settings) -> str:
    if s.sale_followup_interval_minutes is not None:
        m = max(1, s.sale_followup_interval_minutes)
        return f"{m} daqiqadan so‘ng"
    return f"{max(1, s.sale_followup_interval_hours)} soatdan so‘ng"


def is_admin(uid: int | None) -> bool:
    """Admin (jamoa a'zosi) — ADMIN_TELEGRAM_IDS dagi foydalanuvchi."""
    return uid is not None and uid in settings.admin_telegram_ids
