import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.config import settings
from bot.db.repositories import UserRepository
from bot.handlers.helpers import edit_or_answer
from bot.handlers.render import invite_text, present_user_state
from bot.keyboards import back_home_keyboard, main_menu_keyboard
from bot.services.leaderboard import LeaderboardService
from bot.services.subscription import SubscriptionService
from bot.utils import messages as msg

router = Router(name="callbacks")


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(
    cq: CallbackQuery,
    users: UserRepository,
    bot_username: str,
) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None:
        await cq.answer("Qayta /start bosing", show_alert=True)
        return

    ok = await SubscriptionService.is_channel_member(
        cq.bot,
        settings.channel_id,
        cq.from_user.id,
    )
    if not ok:
        await cq.answer()
        await cq.message.answer(msg.NOT_SUBSCRIBED_YET)
        return

    await users.mark_channel_ok(db_user)
    await cq.answer("✅ Kanal tasdiqlandi")

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None:
        return

    await present_user_state(
        db_user=db_user,
        bot_username=bot_username,
        callback=cq,
    )


@router.callback_query(F.data == "ig_ok")
async def cb_ig_ok(
    cq: CallbackQuery,
    users: UserRepository,
    bot_username: str,
) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None:
        await cq.answer("Qayta /start bosing", show_alert=True)
        return

    if not db_user.channel_ok:
        await cq.answer("Avval kanalni tugating", show_alert=True)
        return

    await users.mark_instagram_ok(db_user)
    await cq.answer("Zo‘r!")

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None:
        return

    await present_user_state(
        db_user=db_user,
        bot_username=bot_username,
        callback=cq,
    )


@router.callback_query(F.data == "home")
async def cb_home(
    cq: CallbackQuery,
    state: FSMContext,
    users: UserRepository,
    bot_username: str,
) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return

    await state.clear()

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None:
        await cq.answer()
        return

    if not (db_user.channel_ok and db_user.instagram_ok):
        await present_user_state(
            db_user=db_user,
            bot_username=bot_username,
            callback=cq,
        )
        return

    await edit_or_answer(
        cq,
        msg.MAIN_READY,
        main_menu_keyboard(),
    )


@router.callback_query(F.data == "invite")
async def cb_invite(
    cq: CallbackQuery,
    users: UserRepository,
    bot_username: str,
) -> None:
    if cq.from_user is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None or not (db_user.channel_ok and db_user.instagram_ok):
        await cq.answer("Avval ro‘yxatdan o‘ting", show_alert=True)
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

    text = invite_text(db_user, bot_username)
    await edit_or_answer(cq, text, back_home_keyboard())


@router.callback_query(F.data == "stats")
async def cb_stats(
    cq: CallbackQuery,
    users: UserRepository,
    bot_username: str,
) -> None:
    if cq.from_user is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None or not (db_user.channel_ok and db_user.instagram_ok):
        await cq.answer("Avval ro‘yxatdan o‘ting", show_alert=True)
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

    if db_user.leaderboard_alias:
        alias_block = f"🏷 TOP taxallusi: <b>{html.escape(db_user.leaderboard_alias)}</b>"
    else:
        alias_block = (
            "🏷 TOP taxallusi: <i>yo‘q — «TOP uchun taxallus»dan qo‘shing "
            "(kanalda aniqroq chiqasiz)</i>"
        )
    text = msg.STATS.format(
        code=db_user.referral_code,
        count=db_user.referrals_count,
        alias_block=alias_block,
        tg_id=db_user.tg_id,
    )
    await edit_or_answer(cq, text, back_home_keyboard())


@router.callback_query(F.data == "ranks")
async def cb_ranks(
    cq: CallbackQuery,
    users: UserRepository,
) -> None:
    if cq.from_user is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None or not (db_user.channel_ok and db_user.instagram_ok):
        await cq.answer("Avval ro‘yxatdan o‘ting", show_alert=True)
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

    top = await users.top_referrers(15)
    total = await users.count_users()
    rank = await users.rank_position(db_user)
    text = LeaderboardService.format_bot_ranks_preview(
        db_user,
        top,
        rank,
        total,
        settings.prize_usd,
    )
    await edit_or_answer(cq, text, back_home_keyboard())
    await cq.answer()


@router.callback_query(F.data == "about")
async def cb_about(cq: CallbackQuery, users: UserRepository) -> None:
    if cq.from_user is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None or not (db_user.channel_ok and db_user.instagram_ok):
        await cq.answer("Avval ro‘yxatdan o‘ting", show_alert=True)
        return

    text = msg.ABOUT.format(
        days=settings.leaderboard_interval_days,
        prize=settings.prize_usd,
    )
    await edit_or_answer(cq, text, back_home_keyboard())
