"""Testlar uchun minimal muhit: bot.config import qilinganda majburiy o'zgaruvchilar bo'lsin."""

import os

for _k, _v in {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "BOT_TOKEN": "123456:TEST",
    "CHANNEL_ID": "-1001",
    "CHANNEL_USERNAME": "test_channel",
    "INSTAGRAM_USERNAME": "test_ig",
}.items():
    os.environ.setdefault(_k, _v)
