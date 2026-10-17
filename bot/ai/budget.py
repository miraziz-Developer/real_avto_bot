"""AI xarajatini nazorat qilish: kunlik USD chegarasi va bitta foydalanuvchiga kunlik limit.

Hisob Redis'da (bot restartida yo'qolmaydi); Redis bo'lmasa — jarayon xotirasida.
Kun — Toshkent vaqti bo'yicha (UTC+5). Xarajat taxminiy: provayder qaytargan token soni × narx.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bot.ai.errors import AIBudgetExceeded

logger = logging.getLogger(__name__)

_TASHKENT = timezone(timedelta(hours=5))
_KEY_TTL_SECONDS = 3 * 24 * 3600


@dataclass(frozen=True)
class Prices:
    """1M token narxi (USD)."""

    input_per_m: float
    output_per_m: float
    audio_per_m: float

    def cost(self, *, input_tokens: int = 0, output_tokens: int = 0, audio_tokens: int = 0) -> float:
        text_in = max(0, input_tokens - audio_tokens)
        return (
            text_in * self.input_per_m + audio_tokens * self.audio_per_m + output_tokens * self.output_per_m
        ) / 1_000_000


def today_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(_TASHKENT).strftime("%Y-%m-%d")


class AIBudget:
    def __init__(self, *, daily_usd: float, user_daily_limit: int, redis_url: str | None = None) -> None:
        self.daily_usd = max(0.0, daily_usd)
        self.user_daily_limit = max(0, user_daily_limit)
        self._redis_url = redis_url
        self._redis = None
        self._redis_failed = False
        self._mem: dict[str, float] = {}
        self._alerted_day: str | None = None
        self._alert: Callable[[str], Awaitable[None]] | None = None

    def set_alert(self, fn: Callable[[str], Awaitable[None]] | None) -> None:
        """Chegara tugaganda (kuniga bir marta) chaqiriladi — masalan adminlarga xabar."""
        self._alert = fn

    # --- saqlash -------------------------------------------------------------------------------
    def _r(self):
        if self._redis is None and self._redis_url and not self._redis_failed:
            import redis.asyncio as redis_mod

            self._redis = redis_mod.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _mem_cleanup(self, day: str) -> None:
        stale = [k for k in self._mem if not k.endswith(day)]
        for k in stale:
            self._mem.pop(k, None)

    async def _incr(self, key: str, amount: float) -> float:
        r = self._r()
        if r is not None:
            try:
                v = await r.incrbyfloat(key, amount)
                await r.expire(key, _KEY_TTL_SECONDS)
                return float(v)
            except Exception as e:
                logger.warning("AI budget: Redis ishlamadi, xotiradan foydalaniladi: %s", e)
                self._redis_failed = True
        self._mem[key] = self._mem.get(key, 0.0) + amount
        return self._mem[key]

    async def _get(self, key: str) -> float:
        r = self._r()
        if r is not None:
            try:
                v = await r.get(key)
                return float(v or 0)
            except Exception as e:
                logger.warning("AI budget: Redis ishlamadi, xotiradan foydalaniladi: %s", e)
                self._redis_failed = True
        return self._mem.get(key, 0.0)

    # --- ochiq API -----------------------------------------------------------------------------
    async def spent_today(self) -> float:
        return await self._get(f"ai:spend:{today_key()}")

    async def ensure_available(self) -> None:
        """Kunlik chegara tugagan bo'lsa AIBudgetExceeded (AIError) — chaqiruvchi oddiy javobga o'tadi."""
        if self.daily_usd <= 0:
            return
        if await self.spent_today() >= self.daily_usd:
            raise AIBudgetExceeded(f"Kunlik AI chegarasi (${self.daily_usd:.2f}) tugadi")

    async def add_cost(self, usd: float) -> None:
        if usd <= 0:
            return
        day = today_key()
        self._mem_cleanup(day)
        total = await self._incr(f"ai:spend:{day}", usd)
        if self.daily_usd > 0 and total >= self.daily_usd and self._alerted_day != day:
            self._alerted_day = day
            logger.warning("Kunlik AI chegarasi tugadi: $%.4f / $%.2f", total, self.daily_usd)
            if self._alert is not None:
                try:
                    await self._alert(
                        f"⚠️ <b>Kunlik AI chegarasi tugadi</b> (${self.daily_usd:.2f}).\n"
                        "Ertangacha bot AI'siz ishlaydi: e'lonlar regex bilan o'qiladi, mijozlarga "
                        "bazadan oddiy javob beriladi.\n"
                        "Oshirish: <code>.env</code> → <code>AI_DAILY_BUDGET_USD</code>."
                    )
                except Exception:
                    logger.exception("AI chegarasi haqida adminlarga xabar yuborilmadi")

    async def allow_user(self, key: str) -> bool:
        """Foydalanuvchining bugungi AI so'rovlari limitdan oshmadimi (oshmagan bo'lsa hisoblaydi)."""
        if self.user_daily_limit <= 0:
            return True
        day = today_key()
        self._mem_cleanup(day)
        n = await self._incr(f"ai:user:{key}:{day}", 1)
        return n <= self.user_daily_limit

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
