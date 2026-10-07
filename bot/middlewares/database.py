from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.repositories import AppMetaRepository, CrmRepository, UserRepository


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            data["users"] = UserRepository(session)
            data["app_meta"] = AppMetaRepository(session)
            data["crm"] = CrmRepository(session)
            data["cars"] = CarRepository(session)
            data["leads"] = LeadRepository(session)
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
