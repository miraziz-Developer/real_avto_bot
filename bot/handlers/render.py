from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db.models import User
from bot.keyboards import channel_keyboard, instagram_keyboard, main_menu_keyboard
from bot.utils import messages as msg


async def present_user_state(
    *,
    db_user: User,
    bot_username: str,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
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
