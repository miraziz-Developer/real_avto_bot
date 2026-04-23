from aiogram import Dispatcher

from bot.handlers import callbacks, profile_alias, start


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(profile_alias.router)
    dp.include_router(callbacks.router)
    dp.include_router(start.router)
