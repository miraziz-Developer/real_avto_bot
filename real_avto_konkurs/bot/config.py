import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Atribut talab qilinadi: {name} (.env)")
    return v.strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    database_url: str
    bot_token: str
    channel_id: str
    channel_username: str
    instagram_username: str
    leaderboard_channel_id: str
    prize_usd: int
    leaderboard_interval_days: int


settings = Settings(
    database_url=_req("DATABASE_URL"),
    bot_token=_req("BOT_TOKEN"),
    channel_id=_req("CHANNEL_ID"),
    channel_username=_req("CHANNEL_USERNAME"),
    instagram_username=_req("INSTAGRAM_USERNAME"),
    leaderboard_channel_id=_req("LEADERBOARD_CHANNEL_ID"),
    prize_usd=_int("PRIZE_USD", 50),
    leaderboard_interval_days=_int("LEADERBOARD_INTERVAL_DAYS", 3),
)
