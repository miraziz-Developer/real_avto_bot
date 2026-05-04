from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db.models import User
from bot.db.repositories import UserRepository
from bot.services.subscription import SubscriptionService
from bot.keyboards import (
    channel_keyboard,
    instagram_keyboard,
    main_menu_keyboard,
    root_menu_keyboard,
)
from bot.utils import messages as msg


async def present_root_menu(
    *,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    if callback and callback.message:
        from bot.handlers.helpers import edit_or_answer

        await edit_or_answer(callback, msg.ROOT_WELCOME, root_menu_keyboard())
        return

    if message:
        await message.answer(
            msg.ROOT_WELCOME,
            reply_markup=root_menu_keyboard(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def present_user_state(
    *,
    db_user: User,
    bot_username: str,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    users: UserRepository | None = None,
) -> None:
    if bot is not None and users is not None:
        db_user = await SubscriptionService.sync_channel_ok_from_telegram(bot, users, db_user)
    text, markup = _build_screen(db_user, bot_username)

    if callback and callback.message:
        from bot.handlers.helpers import edit_or_answer

        await edit_or_answer(callback, text, markup)
        return

    if message:
        await message.answer(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return


def _build_screen(db_user: User, _bot_username: str) -> tuple[str, object]:
    if not db_user.channel_ok:
        return msg.WELCOME, channel_keyboard(settings.channel_username)
    if not db_user.instagram_ok:
        return msg.AFTER_CHANNEL, instagram_keyboard(settings.instagram_username)
    return msg.MAIN_READY, main_menu_keyboard()


def invite_text(db_user: User, bot_username: str) -> str:
    link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
    return msg.INVITE_HEADER.format(link=link)
