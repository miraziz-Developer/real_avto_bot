"""Mashinalar bazasi — admin boshqaruvi: karta tugmalari, tuzatish, buyruqlar va statistika."""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    WebAppInfo,
)

from bot.ai import AIError, get_ai
from bot.config import is_admin, settings
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarStatus
from bot.db.repositories import CrmRepository
from bot.handlers.channel_watch import VIDEO_NOTE_PREFIX
from bot.services.car_cards import (
    STATUS_LABELS,
    car_admin_kb,
    car_can_be_posted,
    car_card_html,
    car_channel_caption,
)
from bot.services import channel_import
from bot.services.car_extract import extract_car
from bot.services.car_parser import REQUIRED_FIELDS, parse_admin_edit, parse_price_usd
from bot.services.wishlist_notify import notify_wishlist_matches_car
from bot.utils.numbers import parse_db_id, parse_int_in_range

logger = logging.getLogger(__name__)

router = Router(name="car_admin")

EDIT_HELP = (
    "✏️ <b>Mashina #{cid} ni tuzatish</b>\n\n"
    "O'zgartirmoqchi bo'lgan maydonlarni yozing (har birini yangi qatorda yoki vergul bilan):\n"
    "<code>narx 9800\nyil 2021\nprobeg 76000\nmodel Gentra\nrang oq\npoz 2\nkraska toza\ndtp yo'q</code>\n\n"
    "O'zimiz sotib olgan bo'lsak: <code>xarid 8000</code>, <code>xarajat 300</code>\n"
    "Bekor qilish: /cancel"
)

_ACTIONS = {
    "ok": CarStatus.ACTIVE,
    "keep": CarStatus.ACTIVE,
    "sold": CarStatus.SOLD,
    "res": CarStatus.RESERVED,
    "arch": CarStatus.ARCHIVED,
}


class CarEditStates(StatesGroup):
    waiting_text = State()



def _parse_cb(data: str | None) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    cid = parse_db_id(parts[2]) if len(parts) == 3 else None
    if cid is None:
        return None
    return parts[1], cid


async def _refresh_card(cq: CallbackQuery, car: Car, header: str | None = None) -> None:
    msg = cq.message
    if msg is None or not hasattr(msg, "edit_text"):
        return
    text = car_card_html(car, header=header)
    kb = car_admin_kb(car)
    try:
        if msg.photo:
            if len(text) <= 1024:
                await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await msg.edit_reply_markup(reply_markup=kb)
        else:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("Mashina kartasini yangilab bo'lmadi: %s", e)


@router.callback_query(F.data.startswith("car:"))
async def car_action(cq: CallbackQuery, state: FSMContext, cars: CarRepository, crm: CrmRepository) -> None:
    if cq.from_user is None or not is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    parsed = _parse_cb(cq.data)
    if parsed is None:
        await cq.answer("Noto'g'ri tugma", show_alert=True)
        return
    action, car_id = parsed
    car = await cars.get(car_id)
    if car is None:
        await cq.answer("Mashina topilmadi", show_alert=True)
        return

    if action == "post":
        await _post_to_channel(cq, car, cars, crm)
        return

    if action == "edit":
        await state.set_state(CarEditStates.waiting_text)
        await state.update_data(car_id=car_id)
        await cq.answer()
        if cq.message:
            await cq.message.answer(EDIT_HELP.format(cid=car_id), parse_mode=ParseMode.HTML)
        return

    new_status = _ACTIONS.get(action)
    if new_status is None:
        await cq.answer("Noma'lum amal", show_alert=True)
        return
    if action == "keep":
        await cars.mark_stale_prompted(car)
    changed = await cars.set_status(car, new_status, actor=cq.from_user.id)
    await cars.session.commit()
    label = STATUS_LABELS.get(new_status, new_status)
    await cq.answer(f"{label}" if changed or action == "keep" else "O'zgarish yo'q")
    await _refresh_card(cq, car)
    if changed and new_status == CarStatus.ACTIVE and cq.bot is not None:
        await notify_wishlist_matches_car(cq.bot, crm, cars, car)
        await cars.session.commit()
    if new_status == CarStatus.SOLD and car.is_own and not car.sold_price_usd and cq.message:
        await cq.message.answer(
            f"💰 #{car.id} qanchaga sotildi? Foyda hisobi uchun: <code>/sotildi {car.id} 9500</code>",
            parse_mode=ParseMode.HTML,
        )


async def _post_to_channel(cq: CallbackQuery, car: Car, cars: CarRepository, crm: CrmRepository) -> None:
    # Qatorni qulflaymiz: ikki admin bir vaqtda «Kanalga joylash» bossa — ikkinchisi «allaqachon kanalda» oladi
    await cars.session.flush()
    await cars.session.refresh(car, with_for_update=True)
    if not car_can_be_posted(car):
        await cq.answer("Bu mashinani kanalga joylab bo'lmaydi (rasm yo'q yoki allaqachon kanalda)", show_alert=True)
        return
    if not (car.price_usd and car.year and car.model):
        await cq.answer("Avval «✏️ Tuzatish» orqali narx, yil va modelni kiriting", show_alert=True)
        return
    await cq.answer()
    photos = list(car.photo_file_ids)[:10]
    media = [InputMediaPhoto(media=photos[0], caption=car_channel_caption(car), parse_mode=ParseMode.HTML)]
    media.extend(InputMediaPhoto(media=p) for p in photos[1:])
    try:
        msgs = await cq.bot.send_media_group(settings.channel_id, media)
    except TelegramBadRequest as e:
        if cq.message:
            await cq.message.answer(f"❌ Kanalga joylab bo'lmadi: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
        return
    car.channel_chat_id = msgs[0].chat.id if msgs else None
    car.channel_message_ids = [m.message_id for m in msgs]
    car.published_at = None  # set_status ACTIVE yangi sotuv sanasini qo'yadi
    await cars.set_status(car, CarStatus.ACTIVE, actor=cq.from_user.id if cq.from_user else None)
    await cars.session.commit()
    await notify_wishlist_matches_car(cq.bot, crm, cars, car)
    await cars.session.commit()
    await _refresh_card(cq, car, header="📢 Kanalga joylandi — sotuvda")


@router.message(StateFilter(CarEditStates.waiting_text), Command("cancel"))
async def car_edit_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.")


@router.message(StateFilter(CarEditStates.waiting_text), F.text)
async def car_edit_apply(message: Message, state: FSMContext, cars: CarRepository, crm: CrmRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    car = await cars.get(parse_db_id(str(data.get("car_id") or "")) or 0)
    if car is None:
        await state.clear()
        await message.answer("Mashina topilmadi.")
        return
    values, bad = parse_admin_edit(message.text or "", usd_rate_uzs=settings.usd_rate_uzs)
    if not values:
        await message.answer(
            "Tushunmadim. Masalan: <code>narx 9800</code> yoki <code>yil 2021</code>. Bekor qilish: /cancel",
            parse_mode=ParseMode.HTML,
        )
        return
    changes = await cars.update_fields(car, values, actor=message.from_user.id)
    if car.status == CarStatus.REVIEW and all(getattr(car, f) for f in REQUIRED_FIELDS):
        await cars.set_status(car, CarStatus.ACTIVE, actor=message.from_user.id)
    await cars.session.commit()
    if car.status == CarStatus.ACTIVE and message.bot is not None:
        await notify_wishlist_matches_car(message.bot, crm, cars, car)
        await cars.session.commit()
    await state.clear()
    note = f"\n\n⚠️ Tushunilmadi: {html.escape('; '.join(bad))}" if bad else ""
    header = f"✅ Yangilandi ({len(changes)} ta maydon){note}"
    await message.answer(
        car_card_html(car, header=header),
        parse_mode=ParseMode.HTML,
        reply_markup=car_admin_kb(car),
        disable_web_page_preview=True,
    )


@router.message(Command("mashina"))
async def cmd_car(message: Message, command: CommandObject, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    car_id = parse_db_id(command.args)
    if car_id is None:
        await message.answer("Foydalanish: <code>/mashina 12</code>", parse_mode=ParseMode.HTML)
        return
    car = await cars.get(car_id)
    if car is None:
        await message.answer("Mashina topilmadi.")
        return
    await message.answer(
        car_card_html(car), parse_mode=ParseMode.HTML, reply_markup=car_admin_kb(car), disable_web_page_preview=True
    )


@router.message(Command("sotildi"))
async def cmd_sold(message: Message, command: CommandObject, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split(maxsplit=1)
    car_id = parse_db_id(parts[0]) if parts else None
    if car_id is None:
        await message.answer(
            "Foydalanish: <code>/sotildi 12</code> yoki narx bilan <code>/sotildi 12 9500</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    car = await cars.get(car_id)
    if car is None:
        await message.answer("Mashina topilmadi.")
        return
    sold_price = parse_price_usd(f"narx {parts[1]}", settings.usd_rate_uzs) if len(parts) > 1 else None
    await cars.set_status(car, CarStatus.SOLD, actor=message.from_user.id, sold_price_usd=sold_price)
    if sold_price is not None:
        car.sold_price_usd = sold_price
    await cars.session.commit()
    await message.answer(
        car_card_html(car, header="🔴 Sotildi deb belgilandi"),
        parse_mode=ParseMode.HTML,
        reply_markup=car_admin_kb(car),
        disable_web_page_preview=True,
    )


@router.message(Command("import_kanal"))
async def cmd_import_channel(message: Message, command: CommandObject) -> None:
    """Kanalning oxirgi N ta postini bazaga import qilish (bot ulanmasdan oldingi mashinalar)."""
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    last_id = channel_import.parse_post_ref(parts[0]) if parts else None
    if last_id is None:
        await message.answer(
            "📥 <b>Kanal tarixini import qilish</b>\n\n"
            "Kanaldagi <b>eng oxirgi</b> postning havolasini oling (post ustida ⋮ → «Copy link») va yuboring:\n"
            "<code>/import_kanal https://t.me/kanal/12345</code>\n\n"
            "Ixtiyoriy: nechta post (standart 300) va necha kundan eskisi arxivga (standart 30):\n"
            "<code>/import_kanal https://t.me/kanal/12345 500 45</code>\n\n"
            "Bot har bir postni o'qiydi (videolar ovozi ham tinglanadi) va bazaga qo'shadi. Import paytida "
            "kartalar yuborilmaydi — oxirida hisobot keladi. Shu chatda bir zumda paydo bo'lib o'chadigan "
            "xabarlar ko'rinishi mumkin — bu normal.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return
    count = parse_int_in_range(parts[1], 1, channel_import.MAX_COUNT) if len(parts) > 1 else channel_import.DEFAULT_COUNT
    days = parse_int_in_range(parts[2], 1, 3650) if len(parts) > 2 else channel_import.DEFAULT_ACTIVE_DAYS
    if count is None or days is None:
        await message.answer(f"Post soni 1–{channel_import.MAX_COUNT}, kunlar 1–3650 oralig'ida bo'lsin.")
        return
    if channel_import.is_running():
        await message.answer("⏳ Import allaqachon ishlayapti — tugashini kuting.")
        return
    first = max(1, last_id - count + 1)
    await message.answer(
        f"⏳ Import boshlandi: postlar #{first}–#{last_id}. Taxminan {max(1, count // 100)} daqiqa "
        "(+ videolar tahlili). Tugagach hisobot keladi."
    )
    task = asyncio.create_task(
        channel_import.run_import_for_admin(
            message.bot, message.chat.id, last_id=last_id, count=count, active_days=days
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_background_tasks: set[asyncio.Task] = set()


def _admin_channel_forward(message: Message) -> bool:
    return (
        message.chat.type == "private"
        and message.from_user is not None
        and is_admin(message.from_user.id)
        and channel_import.channel_post_from_forward(message) is not None
    )


@router.message(_admin_channel_forward)
async def on_admin_channel_forward(message: Message) -> None:
    """Admin kanaldagi postni botga forward qildi — shu mashina bazaga qo'shiladi (eski postlarni tanlab qo'shish)."""
    channel_import.queue_forwarded(message.bot, message.chat.id, message)

REANALYZE_MAX_VIDEOS = 4
_SPEECH_PREFIXES = ("[Ovoz]", "[Videoda", "[Video]")


@router.message(Command("qayta"))
async def cmd_reanalyze(message: Message, command: CommandObject, cars: CarRepository) -> None:
    """Mashina videolarini hozirgi AI bilan qayta tinglash va ma'lumotni yangilash.

    Masalan, mashina Groq Whisper davrida (o'zbekchani yomon taniydi) yaratilgan bo'lsa yoki AI xato eshitgan bo'lsa.
    """
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    car_id = parse_db_id((command.args or "").strip())
    car = await cars.get(car_id) if car_id is not None else None
    if car is None:
        await message.answer("Foydalanish: <code>/qayta 12</code> — mashina videolarini qayta tahlil qilish", parse_mode=ParseMode.HTML)
        return
    ai = get_ai()
    if not ai.enabled:
        await message.answer("AI o'chiq — .env ga GEMINI_API_KEY qo'ying.")
        return
    if not car.video_file_ids:
        await message.answer("Bu mashinada video/ovoz yo'q — qayta tahlil qilinadigan narsa yo'q.")
        return
    await message.answer(f"⏳ #{car.id} videolari qayta tinglanmoqda…")
    text_lines = [ln for ln in (car.raw_text or "").splitlines() if not ln.lstrip().startswith(_SPEECH_PREFIXES)]
    heard: list[str] = []
    for fid in car.video_file_ids[:REANALYZE_MAX_VIDEOS]:
        real_id = fid.removeprefix(VIDEO_NOTE_PREFIX)
        try:
            f = await message.bot.get_file(real_id)
            buf = await message.bot.download_file(f.file_path)
            text = await ai.transcribe(buf.read() if buf else b"", filename="video.mp4", mime_type="video/mp4")
        except (AIError, TelegramBadRequest) as e:
            logger.warning("Qayta tahlil: #%s video o'qilmadi: %s", car.id, e)
            continue
        if text:
            heard.append(text)
    if not heard:
        await message.answer("Videolardan hech narsa eshitilmadi (fayl juda katta yoki nutq yo'q).")
        return
    full = "\n".join([*text_lines, *[f"[Ovoz]: {t}" for t in heard]]).strip()
    parsed = await extract_car(full, ai=ai, usd_rate_uzs=settings.usd_rate_uzs)
    changes = await cars.reapply_parsed(car, parsed, raw_text=full)
    await cars.session.commit()
    who = "Gemini" if ai.provider == "gemini" else "Groq Whisper"
    summary = ", ".join(sorted(changes)) if changes else "o'zgarish yo'q"
    await message.answer(
        car_card_html(
            car,
            header=f"🔄 <b>Qayta tahlil qilindi</b> ({who}) — {html.escape(summary)}\n"
            f"🎙 <i>«{html.escape(' '.join(heard)[:300])}»</i>",
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=car_admin_kb(car),
        disable_web_page_preview=True,
    )


async def _send_list(message: Message, cars: CarRepository, status: str, title: str) -> None:
    rows = await cars.list_by_status(status, limit=40)
    if not rows:
        await message.answer(f"{title}: hozircha yo'q.")
        return
    lines = [f"<b>{title}</b> ({len(rows)} ta):", ""]
    for c in rows:
        price = f"${c.price_usd:,}" if c.price_usd else "narx?"
        lines.append(f"<code>#{c.id}</code> {html.escape(c.title)} — {price}")
    lines.append("")
    lines.append("Batafsil: <code>/mashina ID</code>")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("tekshiruv"))
async def cmd_review_list(message: Message, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    await _send_list(message, cars, CarStatus.REVIEW, "🟡 Tekshiruv kutayotgan postlar")


@router.message(Command("sotuvda"))
async def cmd_active_list(message: Message, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    await _send_list(message, cars, CarStatus.ACTIVE, "🟢 Sotuvdagi mashinalar")


def stats_html(s: dict) -> str:
    st = s["by_status"]
    lines = [
        f"📊 <b>Statistika</b> (oxirgi {s['days']} kun)",
        "",
        f"🟢 Sotuvda: <b>{st.get(CarStatus.ACTIVE, 0)}</b> ta — jami ${s['active_value_usd']:,}",
        f"🔵 Bron: {st.get(CarStatus.RESERVED, 0)} · 🟡 Tekshiruvda: {st.get(CarStatus.REVIEW, 0)}",
        f"🆕 Yangi qo'shilgan: {s['new_count']} ta",
        f"🔴 Sotilgan: <b>{s['sold_count']}</b> ta",
    ]
    if s["avg_days_to_sell"] is not None:
        lines.append(f"⏱ O'rtacha sotilish muddati: <b>{s['avg_days_to_sell']}</b> kun")
    if s["own_profit_usd"]:
        lines.append(f"💵 O'zimiz sotib olgan mashinalardan foyda: <b>${s['own_profit_usd']:,}</b>")
    if s["top_sold_models"]:
        lines.append("")
        lines.append("🏆 Eng ko'p sotilganlar:")
        for name, n in s["top_sold_models"]:
            lines.append(f"• {html.escape(name)} — {n} ta")
    return "\n".join(lines)


@router.message(Command("statistika"))
async def cmd_stats(message: Message, command: CommandObject, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    days = parse_int_in_range(arg, 1, 365, allow_separators=False) or 30
    await message.answer(stats_html(await cars.stats(days=days)), parse_mode=ParseMode.HTML)


@router.message(Command("panel"))
async def cmd_panel(message: Message) -> None:
    """Admin panel (CRM) ni bot ichida Mini App bo'lib ochish."""
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    if not settings.crm_url.startswith("https://"):
        await message.answer("CRM_URL (https) sozlanmagan — .env ga qo'shing.")
        return
    await message.answer(
        "🛠 Admin panel — mashinalar, mijozlar, statistika:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🛠 Panelni ochish", web_app=WebAppInfo(url=settings.crm_url))]]
        ),
    )
