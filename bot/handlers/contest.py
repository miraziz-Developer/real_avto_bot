"""Rasmiy konkurs + referal markazi (aktiv konkurs, ro‘yxatdan o‘tish)."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.config import settings
from bot.db.repositories import CrmRepository, UserRepository
from bot.handlers.helpers import edit_or_answer
from bot.handlers.render import present_user_state
from bot.keyboards import contest_hub_keyboard
from bot.services.subscription import SubscriptionService
from bot.utils import messages as msg

router = Router(name="contest")


def _parse_end(row: dict) -> datetime | None:
    end = row.get("end_date")
    if end is None:
        return None
    if isinstance(end, datetime):
        end_dt = end
    else:
        raw = str(end).replace("Z", "+00:00")
        end_dt = datetime.fromisoformat(raw)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return end_dt


def _fmt_end(row: dict) -> str:
    end_dt = _parse_end(row)
    if end_dt is None:
        return "—"
    return end_dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def _contest_ended(row: dict) -> bool:
    end_dt = _parse_end(row)
    if end_dt is None:
        return False
    return end_dt < datetime.now(timezone.utc)


async def _send_contest_hub(
    cq: CallbackQuery,
    *,
    users: UserRepository,
    crm: CrmRepository,
    bot_username: str,
) -> None:
    db_user = await users.get_by_tg_id(cq.from_user.id)  # type: ignore[union-attr]
    if db_user is None or cq.from_user is None:
        return

    contest = await crm.get_active_contest_row()
    client = await crm.get_or_create_client(
        telegram_id=cq.from_user.id,
        full_name=" ".join(
            filter(None, [cq.from_user.first_name, cq.from_user.last_name]),
        )
        or None,
    )

    link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
    header = msg.CONTEST_HUB_HEADER

    if not contest:
        text = header + msg.CONTEST_HUB_NO_ACTIVE + f"\n\n<code>{link}</code>\n👥 Referallar: <b>{db_user.referrals_count}</b> ta"
        await edit_or_answer(cq, text, contest_hub_keyboard(show_join=False))
        return

    title = str(contest.get("title") or "Konkurs")
    prize = str(contest.get("prize") or "—")
    ends = _fmt_end(contest)
    ended = _contest_ended(contest)
    cid = int(contest["id"])
    registered = await crm.client_in_active_contest(cid, client.id)

    if registered:
        status_block = "✅ <b>Rasmiy konkurs:</b> siz ro‘yxatga olgansiz."
        show_join = False
    elif ended:
        status_block = "⏹ <b>Rasmiy konkurs</b> muddati tugagan. Yangi bosqich e’lon qilinadi."
        show_join = False
    else:
        status_block = (
            "📋 <b>Rasmiy konkurs:</b> omadli tanlovda ishtirok etish uchun "
            "«Rasmiy konkursga yozilish»ni bosing (CRM ro‘yxati)."
        )
        show_join = True

    body = msg.CONTEST_HUB_ACTIVE.format(
        title=title,
        prize=prize,
        ends=ends,
        status_block=status_block,
        link=link,
        refs=db_user.referrals_count,
    )
    await edit_or_answer(cq, header + body, contest_hub_keyboard(show_join=show_join))


@router.callback_query(F.data == "open_contest")
async def cb_open_contest(
    cq: CallbackQuery,
    users: UserRepository,
    crm: CrmRepository,
    bot_username: str,
) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None:
        await cq.answer("Qayta /start bosing", show_alert=True)
        return

    if not (db_user.channel_ok and db_user.instagram_ok):
        await present_user_state(
            db_user=db_user,
            bot_username=bot_username,
            callback=cq,
            bot=cq.bot,
            users=users,
        )
        await cq.answer()
        return

    ok = await SubscriptionService.is_channel_member(
        cq.bot,
        settings.channel_id,
        cq.from_user.id,
    )
    if not ok:
        await cq.answer()
        if cq.message:
            await cq.message.answer(msg.REVERIFY_CHANNEL)
        return

    await _send_contest_hub(cq, users=users, crm=crm, bot_username=bot_username)
    await cq.answer()


@router.callback_query(F.data == "contest_join")
async def cb_contest_join(
    cq: CallbackQuery,
    users: UserRepository,
    crm: CrmRepository,
    bot_username: str,
) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None or not (db_user.channel_ok and db_user.instagram_ok):
        await cq.answer(msg.CONTEST_JOIN_NEED_SETUP, show_alert=True)
        return

    ok = await SubscriptionService.is_channel_member(
        cq.bot,
        settings.channel_id,
        cq.from_user.id,
    )
    if not ok:
        await cq.answer()
        if cq.message:
            await cq.message.answer(msg.REVERIFY_CHANNEL)
        return

    client = await crm.get_or_create_client(
        telegram_id=cq.from_user.id,
        full_name=" ".join(
            filter(None, [cq.from_user.first_name, cq.from_user.last_name]),
        )
        or None,
    )
    result = await crm.join_active_contest(client.id)

    if result == "joined":
        await cq.answer(msg.CONTEST_JOIN_OK, show_alert=True)
    elif result == "already":
        await cq.answer(msg.CONTEST_JOIN_ALREADY, show_alert=True)
    elif result == "ended":
        await cq.answer(msg.CONTEST_JOIN_ENDED, show_alert=True)
    else:
        await cq.answer(msg.CONTEST_JOIN_NONE, show_alert=True)

    await _send_contest_hub(cq, users=users, crm=crm, bot_username=bot_username)
