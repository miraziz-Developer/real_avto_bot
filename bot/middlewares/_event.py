"""Update ichidagi haqiqiy hodisani (Message / CallbackQuery) ajratib olish."""

from __future__ import annotations

from aiogram.types import CallbackQuery, Message, TelegramObject, Update


def unwrap_event(event: TelegramObject) -> TelegramObject:
    """`dp.update.middleware` ga `Update` keladi — ichidagi message/callback ni qaytaradi."""
    if isinstance(event, Update):
        inner = event.event
        return inner if inner is not None else event
    return event


def event_user_id(event: TelegramObject) -> int | None:
    if isinstance(event, (Message, CallbackQuery)) and event.from_user is not None:
        return event.from_user.id
    return None
