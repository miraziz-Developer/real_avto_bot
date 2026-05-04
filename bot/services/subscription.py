from aiogram import Bot
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberRestricted,
)

from bot.config import settings
from bot.db.models import User
from bot.db.repositories import UserRepository


class SubscriptionService:
    @staticmethod
    async def is_channel_member(bot: Bot, channel_id: str, user_id: int) -> bool:
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        except Exception:
            return False

        if isinstance(member, (ChatMemberMember, ChatMemberAdministrator, ChatMemberOwner)):
            return True
        if isinstance(member, ChatMemberRestricted):
            return bool(member.is_member)
        return False

    @staticmethod
    async def sync_channel_ok_from_telegram(
        bot: Bot, users: UserRepository, db_user: User
    ) -> User:
        """DBda channel_ok=False bo‘lsa ham, Telegramda a’zo bo‘lsa DBni yangilaydi."""
        ok = await SubscriptionService.is_channel_member(
            bot, settings.channel_id, db_user.tg_id
        )
        if ok and not db_user.channel_ok:
            await users.mark_channel_ok(db_user)
            refreshed = await users.get_by_tg_id(db_user.tg_id)
            return refreshed or db_user
        return db_user
