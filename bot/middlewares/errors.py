"""Kutilmagan xatolarni jurnalga yozadi; polling uzilmasligi uchun foydalanuvchiga yumshoq javob."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

_USER_FRIENDLY = (
    "⚠️ Texnik xato yuz berdi. Iltimos, bir ozdan keyin qayta urinib ko‘ring "
    "yoki /start orqali menyuni yangilang."
)


class UnhandledErrorMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest:
            raise
        except TelegramForbiddenError:
            logger.warning("Foydalanuvchi botni bloklagan yoki chat mumkin emas: %s", type(event).__name__)
            return None
        except Exception:
            logger.exception("Handler xatosi (%s)", type(event).__name__)
            try:
                if (
                    isinstance(event, Message)
                    and event.from_user
                    and event.chat
                    and getattr(event.chat, "type", None) == "private"
                ):
                    await event.answer(_USER_FRIENDLY)
                elif isinstance(event, CallbackQuery) and event.from_user:
                    await event.answer("Texnik xato.", show_alert=True)
            except TelegramBadRequest:
                pass
            return None
