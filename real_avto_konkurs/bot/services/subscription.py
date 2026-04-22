from aiogram import Bot
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberRestricted,
)


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
