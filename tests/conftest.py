"""Testlar uchun xavfsiz muhit.

bot.config import qilinganda load_dotenv() chaqiriladi, lekin u allaqachon o'rnatilgan o'zgaruvchilarni
almashtirmaydi — shu yerda majburiy qiymatlarni beramiz va lokal `.env` dagi haqiqiy kalitlarni
(Groq, Gemini, Instagram, katalog) o'chirib qo'yamiz.
"""

import os

for _k, _v in {
    "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
    "BOT_TOKEN": "1:x",
    "CHANNEL_ID": "@x",
    "CHANNEL_USERNAME": "x",
    "INSTAGRAM_USERNAME": "x",
    "LEADERBOARD_CHANNEL_ID": "@x",
}.items():
    os.environ.setdefault(_k, _v)

for _key, _value in {
    "GROQ_API_KEY": "",
    "GEMINI_API_KEY": "",
    "INSTAGRAM_ENABLED": "false",
    "IG_ACCESS_TOKEN": "",
    "CATALOG_URL": "",
    "ADMIN_TELEGRAM_IDS": "",
}.items():
    os.environ[_key] = _value
