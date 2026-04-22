from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db.repositories import UserRepository
from bot.handlers.helpers import edit_or_answer
from bot.keyboards import alias_prompt_keyboard, main_menu_keyboard
from bot.services.subscription import SubscriptionService
from bot.utils import messages as msg
from bot.utils.alias_valid import normalize_and_validate_alias

router = Router(name="profile_alias")


class AliasStates(StatesGroup):
    waiting_text = State()


def _needs_full_onboarding(db_user) -> bool:
    return not (db_user.channel_ok and db_user.instagram_ok)


@router.callback_query(F.data == "alias_start")
async def cb_alias_start(
    cq: CallbackQuery,
    state: FSMContext,
    users: UserRepository,
) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return

    db_user = await users.get_by_tg_id(cq.from_user.id)
    if db_user is None or _needs_full_onboarding(db_user):
        await cq.answer("Avval ro‘yxatdan o‘ting", show_alert=True)
        return

    ok = await SubscriptionService.is_channel_member(
        cq.bot,
        settings.channel_id,
        cq.from_user.id,
    )
    if not ok:
        await cq.answer()
        await cq.message.answer(msg.REVERIFY_CHANNEL)
        return

    await state.set_state(AliasStates.waiting_text)
    await edit_or_answer(cq, msg.ALIAS_PROMPT, alias_prompt_keyboard())
    await cq.answer()


@router.callback_query(F.data == "alias_cancel")
async def cb_alias_cancel(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if cq.message is None:
        await cq.answer()
        return
    await edit_or_answer(cq, msg.ALIAS_CANCELLED, main_menu_keyboard())
    await cq.answer()


@router.message(
    StateFilter(AliasStates.waiting_text),
    F.text,
    ~F.text.startswith("/"),
)
async def on_alias_text(
    message: Message,
    state: FSMContext,
    users: UserRepository,
) -> None:
    if message.from_user is None or not message.text:
        return

    db_user = await users.get_by_tg_id(message.from_user.id)
    if db_user is None or _needs_full_onboarding(db_user):
        await state.clear()
        return

    try:
        alias = normalize_and_validate_alias(message.text)
    except ValueError as e:
        await message.answer(f"❌ {e.args[0]}")
        return

    await users.set_leaderboard_alias(db_user, alias)
    await state.clear()
    await message.answer(msg.ALIAS_SAVED.format(alias=alias), reply_markup=main_menu_keyboard())
