"""Kutilmagan xatolarni jurnalga yozadi; polling uzilmasligi uchun foydalanuvchiga yumshoq javob.

MUHIM: bu middleware DbSessionMiddleware dan TASHQARIDA (oldin) ro'yxatdan o'tishi kerak —
shunda xato avval DB sessiyasini rollback qiladi, keyin shu yerda ushlanadi.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.middlewares._event import unwrap_event

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
        except TelegramForbiddenError:
            logger.warning("Foydalanuvchi botni bloklagan yoki chat mumkin emas: %s", type(event).__name__)
            return None
        except TelegramBadRequest as e:
            # Masalan «message is not modified», eskirgan callback — foydalanuvchiga xato ko'rsatmaymiz.
            logger.warning("TelegramBadRequest (%s): %s", type(unwrap_event(event)).__name__, e)
            return None
        except Exception:
            inner = unwrap_event(event)
            logger.exception("Handler xatosi (%s)", type(inner).__name__)
            try:
                if (
                    isinstance(inner, Message)
                    and inner.from_user
                    and inner.chat
                    and getattr(inner.chat, "type", None) == "private"
                ):
                    await inner.answer(_USER_FRIENDLY)
                elif isinstance(inner, CallbackQuery) and inner.from_user:
                    await inner.answer("Texnik xato. Qayta urinib ko‘ring.", show_alert=True)
            except Exception:
                pass
            return None
