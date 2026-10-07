
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.config import settings
from bot.db.repositories import UserRepository
from bot.handlers.render import present_root_menu, present_user_state
from bot.services.subscription import SubscriptionService
from bot.utils import messages as msg

router = Router(name="callbacks")


@router.callback_query(F.data == "home_root")
async def cb_home_root(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return

    await state.clear()
    await present_root_menu(callback=cq)


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
        bot=cq.bot,
        users=users,
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
        bot=cq.bot,
        users=users,
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
            bot=cq.bot,
            users=users,
        )
        return

    # Konkurs menusi muzlatilgan — asosiy ish menyuga yo'naltirish
    await present_root_menu(callback=cq)


@router.callback_query(F.data == "invite")
async def cb_invite(
    cq: CallbackQuery,
    users: UserRepository,
    bot_username: str,
) -> None:
    _ = (users, bot_username)  # signature saqlanadi
    await cq.answer("🚧 Taklif qilish funksiyasi vaqtincha o'chirilgan", show_alert=True)


@router.callback_query(F.data == "stats")
async def cb_stats(
    cq: CallbackQuery,
    users: UserRepository,
    bot_username: str,
) -> None:
    _ = (users, bot_username)  # signature saqlanadi
    await cq.answer("🚧 Statistika funksiyasi vaqtincha o'chirilgan", show_alert=True)


@router.callback_query(F.data == "ranks")
async def cb_ranks(
    cq: CallbackQuery,
    users: UserRepository,
) -> None:
    _ = users  # signature saqlanadi
    await cq.answer("🚧 Reyting funksiyasi vaqtincha o'chirilgan", show_alert=True)


@router.callback_query(F.data == "about")
async def cb_about(cq: CallbackQuery, users: UserRepository) -> None:
    _ = users  # signature saqlanadi
    await cq.answer("🚧 Bu bo'lim vaqtincha o'chirilgan", show_alert=True)
