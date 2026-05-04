from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db.repositories import CrmRepository, UserRepository
from bot.handlers.listing_chat import open_listing_buyer_entry
from bot.handlers.render import present_root_menu, present_user_state

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    users: UserRepository,
    crm: CrmRepository,
    bot_username: str,
) -> None:
    if message.from_user is None:
        return

    await state.clear()

    raw = (command.args or "").strip()
    sp = raw.split("_", 1) if raw else ("", "")
    if len(sp) == 2 and sp[0].lower() == "lq" and sp[1].isdigit():
        await users.ensure_user(
            tg_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            start_ref_code=None,
        )
        await open_listing_buyer_entry(message, state, crm, int(sp[1]))
        return

    ref_code = raw.upper() if raw else None

    db_user = await users.ensure_user(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        start_ref_code=ref_code,
    )

    if ref_code:
        await present_user_state(
            db_user=db_user,
            bot_username=bot_username,
            message=message,
            bot=message.bot,
            users=users,
        )
        return

    await present_root_menu(message=message)
