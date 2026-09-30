"""Testlar .env siz ham yig‘ilishi uchun majburiy o‘zgaruvchilarga soxta qiymatlar."""

import os

for _name, _value in {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "BOT_TOKEN": "123456:TEST",
    "CHANNEL_ID": "-1001234567890",
    "CHANNEL_USERNAME": "test_channel",
    "INSTAGRAM_USERNAME": "test_insta",
    "LEADERBOARD_CHANNEL_ID": "-1001234567891",
}.items():
    os.environ.setdefault(_name, _value)
