"""🤖 AI savdo maslahatchisi — Telegramda 24/7 javob, mashina tanlash, issiq mijozni saralash."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.handlers.render import present_root_menu
from bot.services.ai import ClientContext
from bot.services.ai_sales import ConversationStore, handle_ai_message
from bot.utils.phone import normalize_uz_phone

router = Router(name="ai_chat")
logger = logging.getLogger(__name__)

STOP_TEXT = "❌ Suhbatni tugatish"
SHARE_PHONE_TEXT = "📱 Raqamimni yuborish"

AI_INTRO = (
    "🤖 <b>Real Avto AI maslahatchi</b>\n\n"
    "Qanday mashina kerakligini yozing — bazadagi mos variantlarni darhol topib beraman.\n\n"
    "Masalan:\n"
    "• <i>Cobalt 2020, 11 mingdan oshmasin</i>\n"
    "• <i>Kreditga Malibu bormi?</i>\n"
    "• <i>15 ming dollarga oilaviy mashina</i>\n\n"
    "Ko‘rishga kelmoqchi bo‘lsangiz, raqamingizni qoldiring — menejer qo‘ng‘iroq qiladi."
)


class AiChatStates(StatesGroup):
    active = State()


def ai_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SHARE_PHONE_TEXT, request_contact=True)],
            [KeyboardButton(text=STOP_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Qanday mashina qidiryapsiz?",
    )


def _client_ctx(message: Message) -> ClientContext:
    u = message.from_user
    assert u is not None
    name = " ".join(p for p in (u.first_name, u.last_name) if p) or None
    return ClientContext(platform="telegram", social_id=str(u.id), name=name, username=u.username)


async def _start_ai(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AiChatStates.active)
    await message.answer(AI_INTRO, reply_markup=ai_keyboard())


async def _stop_ai(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Suhbat yakunlandi. Savol tug‘ilsa, yana yozing! 🚗", reply_markup=ReplyKeyboardRemove())
    await present_root_menu(message=message)


async def _reply(message: Message, session: AsyncSession, text: str) -> None:
    if message.from_user is None or message.bot is None:
        return
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except Exception:
        pass
    reply = await handle_ai_message(session=session, bot=message.bot, client=_client_ctx(message), text=text)
    if reply.text:
        # AI oddiy matn qaytaradi — HTML sifatida talqin qilmaymiz.
        await message.answer(reply.text, parse_mode=None, reply_markup=ai_keyboard(), disable_web_page_preview=False)


@router.callback_query(F.data == "ai_start")
async def cb_ai_start(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    if not settings.ai_enabled or not isinstance(cq.message, Message):
        return
    await _start_ai(cq.message, state)


@router.message(Command("ai"), F.chat.type == "private")
async def cmd_ai(message: Message, state: FSMContext) -> None:
    if not settings.ai_enabled:
        await message.answer("AI maslahatchi hozircha o‘chirilgan.")
        return
    await _start_ai(message, state)


@router.message(Command("reset"), StateFilter(AiChatStates.active))
async def cmd_reset(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    await ConversationStore(session).reset(_client_ctx(message))
    await message.answer("🧹 Suhbat tarixi tozalandi. Qanday mashina qidiryapsiz?", reply_markup=ai_keyboard())


@router.message(StateFilter(AiChatStates.active), F.text == STOP_TEXT)
async def ai_stop(message: Message, state: FSMContext) -> None:
    await _stop_ai(message, state)


@router.message(StateFilter(AiChatStates.active, None), F.chat.type == "private", F.contact)
async def ai_contact(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not settings.ai_enabled or message.contact is None or message.from_user is None:
        return
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer("Iltimos, o‘zingizning raqamingizni yuboring.", reply_markup=ai_keyboard())
        return
    phone = normalize_uz_phone(message.contact.phone_number) or message.contact.phone_number
    name = " ".join(p for p in (message.contact.first_name, message.contact.last_name) if p)
    await state.set_state(AiChatStates.active)
    await _reply(message, session, f"Mening telefon raqamim: {phone}" + (f", ismim: {name}" if name else ""))


@router.message(StateFilter(AiChatStates.active), F.text, ~F.text.startswith("/"))
async def ai_text(message: Message, session: AsyncSession) -> None:
    await _reply(message, session, message.text or "")


@router.message(StateFilter(AiChatStates.active), F.voice | F.photo | F.video | F.sticker | F.document)
async def ai_unsupported(message: Message) -> None:
    await message.answer(
        "Hozircha faqat matnli savollarga javob beraman ✍️ Qanday mashina kerakligini yozib yuboring.",
        reply_markup=ai_keyboard(),
    )


# Hech qanday oqimda bo‘lmagan foydalanuvchi oddiy matn yozsa (masalan, «Narxi qancha?») —
# javobsiz qolmasin: AI maslahatchi avtomatik ulanadi. Router eng oxirida ro‘yxatdan o‘tadi.
@router.message(StateFilter(None), F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def ai_fallback(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not settings.ai_enabled:
        return
    await state.set_state(AiChatStates.active)
    await _reply(message, session, message.text or "")
