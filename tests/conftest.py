"""Testlar lokal `.env` dagi haqiqiy kalitlarni (Groq, Instagram, katalog) ishlatib yubormasligi uchun.

bot.config import qilinganda load_dotenv() chaqiriladi, lekin u allaqachon o'rnatilgan o'zgaruvchilarni
almashtirmaydi — shu yerda bo'sh/xavfsiz qiymatlar qo'yamiz.
"""

import os

for _key, _value in {
    "GROQ_API_KEY": "",
    "INSTAGRAM_ENABLED": "false",
    "IG_ACCESS_TOKEN": "",
    "CATALOG_URL": "",
    "ADMIN_TELEGRAM_IDS": "",
}.items():
    os.environ[_key] = _value
