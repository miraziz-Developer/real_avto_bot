"""Foydalanuvchiga nisbatan rate limiting — flood xabarlarini oldini olish."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

# (user_id, scene) -> (count, first_timestamp)
_bucket: dict[tuple[int, str], tuple[int, float]] = {}

# sozlamalar
_MAX_MSG_PER_MINUTE = 30
_MAX_CB_PER_MINUTE = 60
_WINDOW_SECONDS = 60.0
_BLOCK_SECONDS = 120.0


def _now() -> float:
    return time.monotonic()


def _check_limit(user_id: int, scene: str, max_count: int) -> bool:
    key = (user_id, scene)
    now = _now()
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
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        scene: str = "default"
        max_count = _MAX_MSG_PER_MINUTE

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            scene = "msg"
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            scene = "cb"
            max_count = _MAX_CB_PER_MINUTE

        if user_id is not None:
            allowed = _check_limit(user_id, scene, max_count)
            if not allowed:
                if isinstance(event, CallbackQuery) and event.from_user:
                    try:
                        await event.answer(
                            "Juda tez bosilyapsiz. Iltimos, biroz kuting.",
                            show_alert=True,
                        )
                    except Exception:
                        pass
                elif isinstance(event, Message) and event.chat and event.chat.type == "private":
                    try:
                        await event.answer(
                            "⏳ Juda ko‘p xabar yubordingiz. Iltimos, 2 daqiqa kutib keyin davom eting."
                        )
                    except Exception:
                        pass
                return None

        return await handler(event, data)
