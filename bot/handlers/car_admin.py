"""Mashinalar bazasi — admin boshqaruvi: karta tugmalari, tuzatish, buyruqlar va statistika."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import Car, CarStatus
from bot.services.car_cards import STATUS_LABELS, car_admin_kb, car_card_html
from bot.db.repositories import CrmRepository
from bot.services.car_parser import parse_admin_edit, parse_price_usd
from bot.services.wishlist_notify import notify_wishlist_matches_car

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


def _is_admin(uid: int | None) -> bool:
    return uid is not None and uid in settings.admin_telegram_ids


def _parse_cb(data: str | None) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return parts[1], int(parts[2])


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
    if cq.from_user is None or not _is_admin(cq.from_user.id):
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


@router.message(StateFilter(CarEditStates.waiting_text), Command("cancel"))
async def car_edit_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.")


@router.message(StateFilter(CarEditStates.waiting_text), F.text)
async def car_edit_apply(message: Message, state: FSMContext, cars: CarRepository, crm: CrmRepository) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    car = await cars.get(int(data.get("car_id") or 0))
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
    if car.status == CarStatus.REVIEW and all((car.brand, car.model, car.year, car.price_usd)):
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
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip().lstrip("#")
    if not arg.isdigit():
        await message.answer("Foydalanish: <code>/mashina 12</code>", parse_mode=ParseMode.HTML)
        return
    car = await cars.get(int(arg))
    if car is None:
        await message.answer("Mashina topilmadi.")
        return
    await message.answer(
        car_card_html(car), parse_mode=ParseMode.HTML, reply_markup=car_admin_kb(car), disable_web_page_preview=True
    )


@router.message(Command("sotildi"))
async def cmd_sold(message: Message, command: CommandObject, cars: CarRepository) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    parts = (command.args or "").split(maxsplit=1)
    if not parts or not parts[0].lstrip("#").isdigit():
        await message.answer(
            "Foydalanish: <code>/sotildi 12</code> yoki narx bilan <code>/sotildi 12 9500</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    car = await cars.get(int(parts[0].lstrip("#")))
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
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    await _send_list(message, cars, CarStatus.REVIEW, "🟡 Tekshiruv kutayotgan postlar")


@router.message(Command("sotuvda"))
async def cmd_active_list(message: Message, cars: CarRepository) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
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
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    days = int(arg) if arg.isdigit() and 1 <= int(arg) <= 365 else 30
    await message.answer(stats_html(await cars.stats(days=days)), parse_mode=ParseMode.HTML)
