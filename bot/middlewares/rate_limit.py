"""Foydalanuvchiga nisbatan rate limiting — flood xabarlarini oldini olish."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.middlewares._event import unwrap_event

logger = logging.getLogger(__name__)

# (user_id, scene) -> (count, first_timestamp)
_bucket: dict[tuple[int, str], tuple[int, float]] = {}

# sozlamalar
_MAX_MSG_PER_MINUTE = 30
_MAX_CB_PER_MINUTE = 60
_WINDOW_SECONDS = 60.0
# Lug'at shu hajmdan oshsa eskirgan yozuvlar tozalanadi (xotira cheksiz o'smasligi uchun).
_SWEEP_THRESHOLD = 5_000
_last_sweep = 0.0


def _now() -> float:
    return time.monotonic()


def _sweep(now: float) -> None:
    global _last_sweep
    if len(_bucket) < _SWEEP_THRESHOLD or now - _last_sweep < _WINDOW_SECONDS:
        return
    _last_sweep = now
    expired = [k for k, (_c, first) in _bucket.items() if now - first > _WINDOW_SECONDS]
    for k in expired:
        _bucket.pop(k, None)


def _check_limit(user_id: int, scene: str, max_count: int) -> bool:
    key = (user_id, scene)
    now = _now()
    _sweep(now)
    count, first = _bucket.get(key, (0, now))
    if now - first > _WINDOW_SECONDS:
        _bucket[key] = (1, now)
        return True
    count += 1
    _bucket[key] = (count, first)
    if count > max_count:
        if count == max_count + 1:
            logger.warning("Rate limit exceeded: user=%s scene=%s", user_id, scene)
        return False
    return True


class RateLimitMiddleware(BaseMiddleware):
    """`dp.update.middleware` sifatida ham, message/callback darajasida ham ishlaydi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        inner = unwrap_event(event)
        user_id: int | None = None
        scene: str = "default"
        max_count = _MAX_MSG_PER_MINUTE

        if isinstance(inner, Message):
            # Kanal postlari / guruhdagi xabarlar cheklanmaydi — faqat shaxsiy chat.
            if inner.chat is not None and inner.chat.type == "private" and inner.from_user:
                user_id = inner.from_user.id
            scene = "msg"
        elif isinstance(inner, CallbackQuery):
            user_id = inner.from_user.id if inner.from_user else None
            scene = "cb"
            max_count = _MAX_CB_PER_MINUTE

        if user_id is not None:
            allowed = _check_limit(user_id, scene, max_count)
            if not allowed:
                count = _bucket.get((user_id, scene), (0, 0.0))[0]
                # Ogohlantirish faqat bir marta — aks holda bot o'zi flood qilib qo'yadi.
                if count == max_count + 1:
                    try:
                        if isinstance(inner, CallbackQuery):
                            await inner.answer(
                                "Juda tez bosilyapsiz. Iltimos, biroz kuting.",
                                show_alert=True,
                            )
                        elif isinstance(inner, Message):
                            await inner.answer(
                                "⏳ Juda ko‘p xabar yubordingiz. Iltimos, 1 daqiqa kutib keyin davom eting."
                            )
                    except Exception:
                        pass
                elif isinstance(inner, CallbackQuery):
                    try:
                        await inner.answer()
                    except Exception:
                        pass
                return None

        return await handler(event, data)
