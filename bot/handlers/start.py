from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.db.cars_repo import CarRepository
from bot.db.leads_repo import LeadRepository
from bot.db.repositories import CrmRepository, UserRepository
from bot.handlers.listing_chat import open_listing_buyer_entry
from bot.handlers.render import present_root_menu, present_user_state
from bot.handlers.sales_agent import open_car_entry
from bot.utils.numbers import parse_db_id

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    users: UserRepository,
    crm: CrmRepository,
    cars: CarRepository,
    leads: LeadRepository,
    bot_username: str,
) -> None:
    if message.from_user is None:
        return

    await state.clear()

    raw = (command.args or "").strip()
    sp = raw.split("_", 1) if raw else ("", "")
    deep_id = parse_db_id(sp[1]) if len(sp) == 2 else None  # ochiq havola — istalgan qiymat kelishi mumkin
    if len(sp) == 2 and sp[0].lower() == "lq" and deep_id is not None:
        await users.ensure_user(
            tg_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            start_ref_code=None,
        )
        await open_listing_buyer_entry(message, state, crm, deep_id)
        return
    if raw.lower() in ("sell", "alert"):
        # Katalogdan: «Mashina sotaman» / «Chiqsa xabar ber» — tegishli oqimni bitta tugma bilan boshlash
        await users.ensure_user(
            tg_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            start_ref_code=None,
        )
        if raw.lower() == "sell":
            text, cb, btn = (
                "💰 Mashinangizni sotmoqchimisiz? E'lonni bir necha qadamda to'ldiring — "
                "kanalimizga chiqadi yoki o'zimiz sotib olishni taklif qilamiz.",
                "ad_start",
                "📢 E'lon berishni boshlash",
            )
        else:
            text, cb, btn = (
                "🔔 Qanday mashina kerakligini saqlang — kanalga chiqishi bilan sizga xabar beramiz.",
                "wishlist_start",
                "🔍 Qidiruvni saqlash",
            )
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn, callback_data=cb)]]),
        )
        return
    if len(sp) == 2 and sp[0].lower() == "car" and deep_id is not None:
        # Mashina haqida suhbat (wishlist xabari, kanal havolasi): agent shu mashina bilan boshlaydi
        await users.ensure_user(
            tg_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            start_ref_code=None,
        )
        await open_car_entry(message, deep_id, leads=leads, cars=cars, crm=crm)
        return

    ref_code = raw.upper() if raw else None

    db_user = await users.ensure_user(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        start_ref_code=ref_code,
    )

    if ref_code:
        await present_user_state(
            db_user=db_user,
            bot_username=bot_username,
            message=message,
            bot=message.bot,
            users=users,
        )
        return

    await present_root_menu(message=message)
