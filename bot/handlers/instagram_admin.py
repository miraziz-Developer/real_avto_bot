"""Admin: Instagram post → mashina bog'lash (ManyChat kommentlari to'g'ri mashina haqida javob olishi uchun).

  • «📸 Instagram post qaysi mashina?» xabaridagi tugma (igpost:<post_id>:<car_id>);
  • /ig HAVOLA_yoki_KOD ID — qo'lda bog'lash (ID 0 — mashina posti emas);
  • /ig — bog'lanmagan postlar ro'yxati.
"""

from __future__ import annotations

import contextlib
import html

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.config import is_admin
from bot.db.cars_repo import CarRepository
from bot.db.models import InstagramPost
from bot.instagram.car_match import get_post, post_ref, remember_post
from bot.utils.numbers import parse_db_id

router = Router(name="instagram_admin")


async def _link(cars: CarRepository, post: InstagramPost, car_id: int) -> str:
    if car_id == 0:
        post.car_id = None
        post.source = "admin"
        return "🚫 Mashina posti emas deb belgilandi — kommentlarga umumiy javob beriladi."
    car = await cars.get(car_id)
    if car is None:
        return f"#{car_id} mashina topilmadi."
    await remember_post(cars, post.ref, car=car, source="admin")
    return f"✅ Post #{car.id} {html.escape(car.title)} ga bog'landi."


@router.callback_query(F.data.startswith("igpost:"))
async def on_pick(cq: CallbackQuery, cars: CarRepository) -> None:
    if cq.from_user is None or not is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = (cq.data or "").split(":")
    post_id = parse_db_id(parts[1]) if len(parts) == 3 else None
    car_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
    post = await cars.session.get(InstagramPost, post_id) if post_id else None
    if post is None or car_id is None:
        await cq.answer("Post topilmadi", show_alert=True)
        return
    result = await _link(cars, post, car_id)
    await cars.session.commit()
    await cq.answer(html.unescape(result)[:200])
    if cq.message is not None:
        with contextlib.suppress(TelegramBadRequest):
            await cq.message.edit_text(
                f"{cq.message.html_text}\n\n{result}", parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )


@router.message(Command("ig"))
async def cmd_ig(message: Message, command: CommandObject, cars: CarRepository) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    args = (command.args or "").split()
    if not args:
        rows = (
            await cars.session.execute(
                select(InstagramPost).where(InstagramPost.car_id.is_(None)).order_by(InstagramPost.id.desc()).limit(15)
            )
        ).scalars().all()
        if not rows:
            await message.answer("Bog'lanmagan Instagram post yo'q.\nBog'lash: /ig HAVOLA ID")
            return
        lines = ["📸 <b>Bog'lanmagan Instagram postlar</b>"]
        lines += [f"• {html.escape(p.permalink or p.ref)} → <code>/ig {html.escape(p.ref)} ID</code>" for p in rows]
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return
    if len(args) != 2 or not args[1].lstrip("#").isdigit():
        await message.answer("Format: /ig HAVOLA_yoki_KOD ID   (masalan: /ig https://instagram.com/p/AbC123/ 17)")
        return
    ref = post_ref(args[0] if args[0].isdigit() else None, args[0]) or args[0]
    post = await get_post(cars, ref) or await remember_post(cars, ref, car=None, permalink=args[0] if "/" in args[0] else None)
    result = await _link(cars, post, int(args[1].lstrip("#")))
    await cars.session.commit()
    await message.answer(result, parse_mode=ParseMode.HTML)
