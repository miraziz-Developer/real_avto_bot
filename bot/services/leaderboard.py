import html
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.enums import ParseMode

from bot.config import settings
from bot.db.models import User


class LeaderboardService:
    MEDALS = ("🥇", "🥈", "🥉")

    @classmethod
    def format_top_message(cls, rows: list[User], prize_usd: int) -> str:
        lines: list[str] = [
            "🏆 <b>REFERAL MUSOBAQASI — TOP 15</b>",
            "",
            f"💰 Eng ko‘p do‘st taklif qilgan ishtirokchi <b>{prize_usd}$</b> gacha mukofot uchun kurashmoqda!",
            f"📅 {cls._now_str()}",
            "",
            "Har qatorda: <b>ko‘rinish nomi</b> · @username (bo‘lsa) · "
            "<code>id:…</code> — yagona identifikator (bir xil ismlarni ajratish).",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        if not rows:
            lines.append("<i>Hozircha statistika yo‘q — birinchi bo‘ling!</i>")
            lines.append("")
            lines.append("Do‘stlaringizni taklif qilib, ro‘yxatda o‘rn oling 👇")
            return "\n".join(lines)

        for i, u in enumerate(rows, start=1):
            medal = cls.MEDALS[i - 1] if i <= 3 else f"<b>{i}.</b>"
            row_body = cls.format_user_row_public(u)
            lines.append(f"{medal} {row_body} — <b>{u.referrals_count}</b> ta")
        lines.extend(
            [
                "",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
                "🔥 O‘z linkingizni ulashing va keyingi reytingda birinchi bo‘ling!",
            ]
        )
        return "\n".join(lines)

    @classmethod
    def format_user_row_public(cls, u: User) -> str:
        """Kanalga chiqadigan bir qator: taxallus yoki TG ism, @havola, id."""
        chunks: list[str] = []
        if u.leaderboard_alias:
            chunks.append(f"<b>{html.escape(u.leaderboard_alias)}</b>")
        else:
            chunks.append(html.escape(cls._telegram_display_name(u)))
        if u.username:
            un = html.escape(u.username.lstrip("@"))
            chunks.append(f'<a href="https://t.me/{un}">@{un}</a>')
        chunks.append(f"id:<code>{u.tg_id}</code>")
        return " · ".join(chunks)

    @staticmethod
    def _telegram_display_name(u: User) -> str:
        parts = [p for p in (u.first_name, u.last_name) if p]
        if parts:
            return " ".join(parts)
        if u.username:
            return f"@{u.username}"
        return f"user_{u.tg_id}"

    @staticmethod
    def _now_str() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    @classmethod
    async def post_to_channel(cls, bot: Bot, rows: list[User]) -> None:
        text = cls.format_top_message(rows, settings.prize_usd)
        await bot.send_message(
            chat_id=settings.leaderboard_channel_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
