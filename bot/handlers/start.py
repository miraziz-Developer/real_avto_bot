from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db.repositories import UserRepository
from bot.handlers.render import present_user_state

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    users: UserRepository,
    bot_username: str,
) -> None:
    if message.from_user is None:
        return

    await state.clear()

    raw = (command.args or "").strip()
    ref_code = raw.upper() if raw else None

    db_user = await users.ensure_user(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        start_ref_code=ref_code,
    )

    await present_user_state(
        db_user=db_user,
        bot_username=bot_username,
        message=message,
    )
