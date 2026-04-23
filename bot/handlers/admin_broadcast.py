import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.base import get_session_factory
from bot.db.repositories import UserRepository

logger = logging.getLogger(__name__)

router = Router(name="admin_broadcast")


class BroadcastStates(StatesGroup):
    waiting_content = State()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids


async def _start_broadcast_flow(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id
    if not settings.admin_telegram_ids:
        return
    if not _is_admin(uid):
        return
    await state.set_state(BroadcastStates.waiting_content)
    await message.answer(
        "📣 <b>Massa xabar</b>\n\n"
        "Keyingi yuboradigan xabaringiz <b>barcha bot foydalanuvchilariga</b> "
        "(shaxsiy chatga) nusxalanadi: matn, rasm, video, ovoz, dumaloq video va hokazo.\n\n"
        "Bekor qilish: /cancel",
    )


@router.message(Command("message_to_users"), F.chat.type == ChatType.PRIVATE)
async def cmd_message_to_users(message: Message, state: FSMContext) -> None:
    await _start_broadcast_flow(message, state)


@router.message(Command("broadcast"), F.chat.type == ChatType.PRIVATE)
async def cmd_broadcast_alias(message: Message, state: FSMContext) -> None:
    await _start_broadcast_flow(message, state)


@router.message(Command("cancel"), StateFilter(BroadcastStates.waiting_content))
async def cmd_cancel_broadcast(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("Bekor qilindi.")


@router.message(StateFilter(BroadcastStates.waiting_content), F.chat.type == ChatType.PRIVATE)
async def on_broadcast_content(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id
    if not _is_admin(uid):
        await state.clear()
        return

    if message.text and message.text.startswith("/"):
        await message.answer("Oddiy xabar yuboring (media bilan ham) yoki /cancel")
        return

    from_chat_id = message.chat.id
    message_id = message.message_id

    await state.clear()
    await message.answer(
        "⏳ Yuborish boshlandi. Foydalanuvchilar ko‘p bo‘lsa, bir necha daqiqa davom etishi mumkin. "
        "Tugagach, shu yerga hisobot yuboraman.",
    )

    asyncio.create_task(
        _broadcast_job(bot, get_session_factory(), from_chat_id, message_id, uid),
        name="broadcast_users",
    )


async def _broadcast_job(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    from_chat_id: int,
    message_id: int,
    admin_tg_id: int,
) -> None:
    ok = blocked = errors = skipped = 0
    try:
        async with session_factory() as session:
            ur = UserRepository(session)
            targets = await ur.list_all_tg_ids()
            await session.commit()
    except Exception:
        logger.exception("Broadcast: bazadan ID olish")
        try:
            await bot.send_message(admin_tg_id, "❌ Bazadan foydalanuvchilar ro‘yxati olinmadi.")
        except Exception:
            pass
        return

    for tg_id in targets:
        if tg_id == admin_tg_id:
            skipped += 1
            continue
        sent = False
        for _ in range(2):
            try:
                await bot.copy_message(
                    chat_id=tg_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
                ok += 1
                sent = True
                break
            except TelegramRetryAfter as e:
                wait = float(e.retry_after) + 0.5
                logger.warning("FloodWait %s s", wait)
                await asyncio.sleep(wait)
            except TelegramForbiddenError:
                blocked += 1
                sent = True
                break
            except TelegramBadRequest as e:
                logger.info("copy_message bad: %s -> %s", tg_id, e)
                errors += 1
                sent = True
                break
            except Exception as e:
                logger.info("copy_message err: %s -> %s", tg_id, e)
                errors += 1
                sent = True
                break
        if not sent:
            errors += 1
        await asyncio.sleep(0.04)

    summary = (
        f"✅ <b>Yuborish tugadi</b>\n\n"
        f"Muvaffaqiyat: <b>{ok}</b>\n"
        f"Bot bloklangan / yo‘q: <b>{blocked}</b>\n"
        f"Xato: <b>{errors}</b>\n"
        f"O‘zingiz (o‘tkazildi): <b>{skipped}</b>"
    )
    try:
        await bot.send_message(admin_tg_id, summary)
    except Exception:
        logger.exception("Admin hisobot yuborilmadi")
