from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import TelegramObject


class BotUsernameMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot = data["bot"]
        dp: Dispatcher = data["dispatcher"]
        if "bot_username" not in dp.workflow_data:
            me = await bot.get_me()
            dp.workflow_data["bot_username"] = me.username
        data["bot_username"] = dp.workflow_data["bot_username"]
        return await handler(event, data)
