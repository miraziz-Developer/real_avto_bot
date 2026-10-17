"""Qidiruv (wishlist): katalogdan marka/model, bir nechta saqlangan qidiruv, o'chirish."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from aiogram.exceptions import TelegramBadRequest

from bot.config import settings
from bot.data.car_catalog import (
    BRAND_MODELS,
    BRAND_ORDER,
    BRANDS_PER_PAGE,
    GRID_COLS,
    MODELS_PER_PAGE,
    subvariants_for,
)
from bot.db.models import ListingSubmissionStatus
from bot.db.repositories import CrmRepository
from bot.handlers.ad_listing import listing_caption_from_sub_public, truncate_caption_html
from bot.handlers.form_limits import BUDGET_USD_MAX, BUDGET_USD_MIN, CAR_YEAR_MAX, CAR_YEAR_MIN
from bot.utils.numbers import parse_int, parse_int_in_range
from bot.utils import messages as msg
from bot.utils.contact_html import sales_phones_links_html
from bot.utils.currency import fmt_usd
from bot.utils.listing_links import channel_post_url

router = Router(name="wishlist")

WISHLIST_MAX_ACTIVE = 30

_COND_ROWS = [
    ("✨ Ideal", "ideal"),
    ("👍 Yaxshi", "yaxshi"),
    ("😐 Qoniqarli", "qoniqarli"),
    ("🔧 Ta'mir", "tamir"),
]


class WishlistStates(StatesGroup):
    pick_brand = State()
    brand_custom = State()
    pick_model = State()
    pick_model_variant = State()
    model_custom = State()
    year_min = State()
    year_max = State()
    budget_max = State()
    budget_min = State()
    condition = State()


def _hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi qidiruv saqlash", callback_data="wishlist_new")],
            [InlineKeyboardButton(text="📋 Saqlangan qidiruvlarim", callback_data="wishlist_list")],
            [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")],
        ]
    )


def _brand_page_kb(page: int) -> InlineKeyboardMarkup:
    n = len(BRAND_ORDER)
    start = page * BRANDS_PER_PAGE
    chunk = BRAND_ORDER[start : start + BRANDS_PER_PAGE]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 2):
        row: list[InlineKeyboardButton] = []
        for j in range(2):
            k = i + j
            if k >= len(chunk):
                break
            bi = start + k
            label = chunk[k]
            row.append(InlineKeyboardButton(text=label, callback_data=f"wl_bi:{bi}"))
        rows.append(row)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"wl_bpg:{page - 1}"))
    if start + BRANDS_PER_PAGE < n:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"wl_bpg:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_kb_paged(brand_idx: int, page: int) -> InlineKeyboardMarkup | None:
    if brand_idx < 0 or brand_idx >= len(BRAND_ORDER):
        return None
    brand = BRAND_ORDER[brand_idx]
    models = BRAND_MODELS.get(brand) or []
    if not models:
        return None
    n = len(models)
    per = MODELS_PER_PAGE
    start = max(0, page) * per
    if start >= n:
        start = 0
        page = 0
    chunk = models[start : start + per]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), GRID_COLS):
        row: list[InlineKeyboardButton] = []
        for j in range(GRID_COLS):
            k = i + j
            if k >= len(chunk):
                break
            mi = start + k
            m = chunk[k]
            row.append(InlineKeyboardButton(text=m, callback_data=f"wl_mm:{brand_idx}:{mi}"))
        rows.append(row)
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"wl_mpg:{brand_idx}:{page - 1}"))
    if start + per < n:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"wl_mpg:{brand_idx}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⏭ Model — o'tkazish", callback_data=f"wl_ms:{brand_idx}")])
    rows.append([InlineKeyboardButton(text="✍️ Boshqa model (matn)", callback_data=f"wl_mo:{brand_idx}")])
    rows.append([InlineKeyboardButton(text="◀️ Markaga qaytish", callback_data="wl_back_brand_pick")])
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _variant_kb(variants: list[str], brand_idx: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(variants), GRID_COLS):
        row: list[InlineKeyboardButton] = []
        for j in range(GRID_COLS):
            vi = i + j
            if vi >= len(variants):
                break
            row.append(
                InlineKeyboardButton(
                    text=variants[vi],
                    callback_data=f"wl_mvc:{vi}",
                )
            )
        rows.append(row)
    rows.append(
        [InlineKeyboardButton(text="◀️ Modellarga qaytish", callback_data=f"wl_rm:{brand_idx}")],
    )
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _after_save_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Saqlangan qidiruvlarim", callback_data="wishlist_list")],
            [InlineKeyboardButton(text="➕ Yana qidiruv saqlash", callback_data="wishlist_new")],
            [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")],
        ],
    )


def _cond_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t, callback_data=f"wl_c:{k}")] for t, k in _COND_ROWS]
    rows.append([InlineKeyboardButton(text="⏭ Holat farqi yo'q", callback_data="wl_c:_skip")])
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_wish_line(w) -> str:
    m = w.model or "—"
    lo = fmt_usd(w.budget_min) if w.budget_min else "—"
    hi = fmt_usd(w.budget_max)
    return (
        f"• <b>#{w.id}</b> {html.escape(w.brand)} {html.escape(m)}\n"
        f"  <i>{w.year_min}–{w.year_max} yil · {lo} … {hi}</i>"
    )


@router.callback_query(F.data == "wishlist_start")
async def wishlist_hub(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if cq.message:
        await cq.message.edit_text(
            "🔍 <b>Qidiruv saqlash</b>\n\n"
            "Mos keladigan e'lon kanalga chiqqanida sizga xabar beramiz.\n\n"
            "Marka va model <b>katalogdan</b> tanlanadi — xato yoki kirillcha yozuv muammosi bo‘lmaydi.\n"
            "Bir nechta qidiruv saqlashingiz mumkin (maksimum "
            f"<b>{WISHLIST_MAX_ACTIVE}</b> ta faol).",
            parse_mode=ParseMode.HTML,
            reply_markup=_hub_kb(),
        )
    await cq.answer()


@router.callback_query(F.data == "wishlist_list")
async def wishlist_list(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    await state.clear()
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return
    client = await crm.get_or_create_client(
        telegram_id=cq.from_user.id,
        full_name=" ".join(filter(None, [cq.from_user.first_name, cq.from_user.last_name])) or None,
    )
    rows = await crm.list_active_wishlists(client.id, limit=WISHLIST_MAX_ACTIVE)
    if not rows:
        text = (
            "📋 <b>Saqlangan qidiruvlar</b>\n\n"
            "Hozircha faol qidiruv yo‘q. «Yangi qidiruv saqlash»dan boshlang."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Yangi qidiruv", callback_data="wishlist_new")],
                [InlineKeyboardButton(text="◀️ Orqaga", callback_data="wishlist_start")],
            ]
        )
    else:
        lines = "\n\n".join(_fmt_wish_line(w) for w in rows)
        text = f"📋 <b>Faol qidiruvlar</b> ({len(rows)})\n\n{lines}\n\nKerakmasini 🗑 bilan o‘chiring."
        del_rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text=f"🗑 #{w.id}", callback_data=f"wl_del:{w.id}")] for w in rows
        ]
        del_rows.append([InlineKeyboardButton(text="➕ Yangi qidiruv", callback_data="wishlist_new")])
        del_rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="wishlist_start")])
        kb = InlineKeyboardMarkup(inline_keyboard=del_rows)
    await cq.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data == "wishlist_new")
async def wishlist_new(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return
    client = await crm.get_or_create_client(
        telegram_id=cq.from_user.id,
        full_name=" ".join(filter(None, [cq.from_user.first_name, cq.from_user.last_name])) or None,
    )
    n = await crm.count_active_wishlists(client.id)
    if n >= WISHLIST_MAX_ACTIVE:
        await cq.answer(
            f"Faol qidiruvlar soni limitda ({WISHLIST_MAX_ACTIVE}). "
            "Avval «Saqlangan qidiruvlarim»dan kerakmasini o‘chiring.",
            show_alert=True,
        )
        return
    await state.clear()
    await state.set_state(WishlistStates.pick_brand)
    await state.update_data(brand_list_page=0)
    await cq.message.edit_text(
        "🔍 <b>Yangi qidiruv</b>\n\n"
        "<b>Marka</b>ni tanlang (3 ustun, sahifalar ◀️ ▶️).\n"
        "«Boshqa» — markani o‘zingiz yozasiz.",
        parse_mode=ParseMode.HTML,
        reply_markup=_brand_page_kb(0),
    )
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_brand), F.data.startswith("wl_bpg:"))
async def wl_brand_page(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    raw = (cq.data or "").split(":", 1)[-1]
    if not raw.lstrip("-").isdigit():
        await cq.answer()
        return
    page = int(raw)
    max_p = max(0, (len(BRAND_ORDER) - 1) // BRANDS_PER_PAGE)
    page = max(0, min(page, max_p))
    await state.update_data(brand_list_page=page)
    await cq.message.edit_reply_markup(reply_markup=_brand_page_kb(page))
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_brand), F.data.startswith("wl_bi:"))
async def wl_brand_pick(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    part = (cq.data or "").split(":", 1)[-1]
    if not part.isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    bi = int(part)
    if bi < 0 or bi >= len(BRAND_ORDER):
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    brand = BRAND_ORDER[bi]
    if brand == "Boshqa":
        await state.set_state(WishlistStates.brand_custom)
        await cq.message.edit_text(
            "✍️ <b>Markani yozing</b> (lotin yoki o‘zbek lotin, masalan: <code>Lexus</code>):",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Markaga qaytish", callback_data="wishlist_new")],
                    [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")],
                ]
            ),
        )
        await cq.answer()
        return
    await state.update_data(brand=brand, brand_idx=bi, model_page=0)
    mk = _model_kb_paged(bi, 0)
    if mk is None:
        await state.set_state(WishlistStates.year_min)
        await cq.message.edit_text(
            f"📅 <b>{html.escape(brand)}</b> — model tanlanmadi.\n\n"
            f"Eng past yil ({CAR_YEAR_MIN}–{CAR_YEAR_MAX}):",
            parse_mode=ParseMode.HTML,
        )
        await cq.answer()
        return
    await state.set_state(WishlistStates.pick_model)
    await cq.message.edit_text(
        f"🚗 <b>{html.escape(brand)}</b> — modelni tanlang (3 ustun, sahifalar ◀️ ▶️):",
        parse_mode=ParseMode.HTML,
        reply_markup=mk,
    )
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_model), F.data.startswith("wl_mpg:"))
async def wl_model_page_nav(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await cq.answer()
        return
    bi, page = int(parts[1]), int(parts[2])
    models = BRAND_MODELS.get(BRAND_ORDER[bi]) if 0 <= bi < len(BRAND_ORDER) else None
    if not models:
        await cq.answer()
        return
    max_p = max(0, (len(models) - 1) // MODELS_PER_PAGE)
    page = max(0, min(page, max_p))
    await state.update_data(model_page=page)
    mk = _model_kb_paged(bi, page)
    if mk is None:
        await cq.answer()
        return
    await cq.message.edit_reply_markup(reply_markup=mk)
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_model), F.data == "wl_back_brand_pick")
async def wl_back_to_brand_list(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    data = await state.get_data()
    page = int(data.get("brand_list_page") or 0)
    max_p = max(0, (len(BRAND_ORDER) - 1) // BRANDS_PER_PAGE)
    page = max(0, min(page, max_p))
    await state.set_state(WishlistStates.pick_brand)
    await cq.message.edit_text(
        "🔍 <b>Yangi qidiruv</b>\n\n"
        "<b>Marka</b>ni tanlang (3 ustun, sahifalar ◀️ ▶️).\n"
        "«Boshqa» — markani o‘zingiz yozasiz.",
        parse_mode=ParseMode.HTML,
        reply_markup=_brand_page_kb(page),
    )
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_model_variant), F.data.startswith("wl_rm:"))
async def wl_variant_back_models(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    tail = (cq.data or "").split(":", 1)[-1]
    if not tail.isdigit():
        await cq.answer()
        return
    bi = int(tail)
    data = await state.get_data()
    page = int(data.get("model_page") or 0)
    if bi < 0 or bi >= len(BRAND_ORDER):
        await cq.answer()
        return
    brand = BRAND_ORDER[bi]
    await state.set_state(WishlistStates.pick_model)
    await state.update_data(variant_options=None, pending_base_model=None)
    mk = _model_kb_paged(bi, page)
    if mk is None:
        await cq.answer()
        return
    await cq.message.edit_text(
        f"🚗 <b>{html.escape(brand)}</b> — modelni tanlang (3 ustun, sahifalar ◀️ ▶️):",
        parse_mode=ParseMode.HTML,
        reply_markup=mk,
    )
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_model_variant), F.data.startswith("wl_mvc:"))
async def wl_variant_chosen(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    tail = (cq.data or "").split(":", 1)[-1]
    if not tail.isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    vi = int(tail)
    data = await state.get_data()
    opts = list(data.get("variant_options") or [])
    bi = data.get("brand_idx")
    if vi < 0 or vi >= len(opts) or not isinstance(bi, int):
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    brand = BRAND_ORDER[bi] if 0 <= bi < len(BRAND_ORDER) else ""
    model = opts[vi]
    await state.update_data(brand=brand, brand_idx=bi, model=model, variant_options=None, pending_base_model=None)
    await state.set_state(WishlistStates.year_min)
    await cq.message.edit_text(
        f"📅 <b>{html.escape(brand)} {html.escape(model)}</b>\n\n"
        f"Eng past yil ({CAR_YEAR_MIN}–{CAR_YEAR_MAX}):",
        parse_mode=ParseMode.HTML,
    )
    await cq.answer()


@router.message(StateFilter(WishlistStates.brand_custom), F.text)
async def wl_brand_custom_text(message: Message, state: FSMContext) -> None:
    b = (message.text or "").strip()
    if len(b) < 2:
        await message.answer("Marka kamida 2 belgi bo‘lsin.")
        return
    b = b[:100]
    await state.update_data(brand=b, brand_idx=None)
    await state.set_state(WishlistStates.model_custom)
    await message.answer(
        "🚗 <b>Modelni yozing</b> (yoki «o'tkazish» uchun tugma):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Model — o'tkazish", callback_data="wl_mskip_custom")],
                [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")],
            ]
        ),
    )


@router.callback_query(StateFilter(WishlistStates.model_custom), F.data == "wl_mskip_custom")
async def wl_model_custom_skip(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(model=None)
    await state.set_state(WishlistStates.year_min)
    if cq.message:
        await cq.message.edit_text(
            f"📅 Eng past yil ({CAR_YEAR_MIN}–{CAR_YEAR_MAX}):",
            parse_mode=ParseMode.HTML,
        )
    await cq.answer()


@router.message(StateFilter(WishlistStates.model_custom), F.text)
async def wl_model_custom_text(message: Message, state: FSMContext) -> None:
    m = (message.text or "").strip()
    await state.update_data(model=m[:100] if m else None)
    await state.set_state(WishlistStates.year_min)
    await message.answer(f"📅 Eng past yil ({CAR_YEAR_MIN}–{CAR_YEAR_MAX}):")


@router.callback_query(StateFilter(WishlistStates.pick_model), F.data.startswith("wl_ms:"))
async def wl_model_skip_idx(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(model=None)
    await state.set_state(WishlistStates.year_min)
    if cq.message:
        await cq.message.edit_text(
            f"📅 Eng past yil ({CAR_YEAR_MIN}–{CAR_YEAR_MAX}):",
            parse_mode=ParseMode.HTML,
        )
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_model), F.data.startswith("wl_mo:"))
async def wl_model_other_idx(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WishlistStates.model_custom)
    if cq.message:
        await cq.message.edit_text(
            "✍️ <b>Modelni yozing</b>:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⏭ Model — o'tkazish", callback_data="wl_mskip_custom")],
                    [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home_root")],
                ]
            ),
        )
    await cq.answer()


@router.callback_query(StateFilter(WishlistStates.pick_model), F.data.startswith("wl_mm:"))
async def wl_model_pick(cq: CallbackQuery, state: FSMContext) -> None:
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    bi, mi = int(parts[1]), int(parts[2])
    if bi < 0 or bi >= len(BRAND_ORDER):
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    brand = BRAND_ORDER[bi]
    models = BRAND_MODELS.get(brand) or []
    if mi < 0 or mi >= len(models):
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    model = models[mi]
    subs = subvariants_for(brand, model)
    if subs:
        await state.update_data(
            brand=brand,
            brand_idx=bi,
            model=None,
            pending_base_model=model,
            variant_options=subs,
        )
        await state.set_state(WishlistStates.pick_model_variant)
        if cq.message:
            await cq.message.edit_text(
                f"🚗 <b>{html.escape(brand)} {html.escape(model)}</b> — aniq turini tanlang:",
                parse_mode=ParseMode.HTML,
                reply_markup=_variant_kb(subs, bi),
            )
        await cq.answer()
        return

    await state.update_data(brand=brand, brand_idx=bi, model=model)
    await state.set_state(WishlistStates.year_min)
    if cq.message:
        await cq.message.edit_text(
            f"📅 <b>{html.escape(brand)} {html.escape(model)}</b>\n\n"
            f"Eng past yil ({CAR_YEAR_MIN}–{CAR_YEAR_MAX}):",
            parse_mode=ParseMode.HTML,
        )
    await cq.answer()


@router.callback_query(F.data.startswith("wl_del:"))
async def wl_delete(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    await state.clear()
    if cq.from_user is None:
        await cq.answer()
        return
    tail = (cq.data or "").split(":", 1)[-1]
    if not tail.isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    wid = int(tail)
    client = await crm.get_or_create_client(
        telegram_id=cq.from_user.id,
        full_name=" ".join(filter(None, [cq.from_user.first_name, cq.from_user.last_name])) or None,
    )
    cid = client.id
    ok = await crm.deactivate_wishlist_for_owner(wishlist_id=wid, client_id=cid)
    if ok:
        await cq.answer("O‘chirildi.")
    else:
        await cq.answer("Topilmadi yoki allaqachon o‘chirilgan.", show_alert=True)
    if cq.message:
        rows = await crm.list_active_wishlists(cid, limit=WISHLIST_MAX_ACTIVE)
        if not rows:
            await cq.message.edit_text(
                "📋 <b>Saqlangan qidiruvlar</b>\n\n"
                "Hozircha faol qidiruv yo‘q.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="➕ Yangi qidiruv", callback_data="wishlist_new")],
                        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="wishlist_start")],
                    ]
                ),
            )
        else:
            lines = "\n\n".join(_fmt_wish_line(w) for w in rows)
            text = f"📋 <b>Faol qidiruvlar</b> ({len(rows)})\n\n{lines}\n\nKerakmasini 🗑 bilan o‘chiring."
            del_rows = [[InlineKeyboardButton(text=f"🗑 #{w.id}", callback_data=f"wl_del:{w.id}")] for w in rows]
            del_rows.append([InlineKeyboardButton(text="➕ Yangi qidiruv", callback_data="wishlist_new")])
            del_rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="wishlist_start")])
            await cq.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=del_rows),
            )


@router.message(StateFilter(WishlistStates.year_min), F.text)
async def wl_year_min(message: Message, state: FSMContext) -> None:
    y = parse_int_in_range(message.text, CAR_YEAR_MIN, CAR_YEAR_MAX, allow_separators=False)
    if y is None:
        await message.answer(f"Yil raqam bilan, {CAR_YEAR_MIN}–{CAR_YEAR_MAX} orasida bo'lsin.")
        return
    await state.update_data(year_min=y)
    await state.set_state(WishlistStates.year_max)
    await message.answer("📅 Eng yuqori yil:")


@router.message(StateFilter(WishlistStates.year_max), F.text)
async def wl_year_max(message: Message, state: FSMContext) -> None:
    y = parse_int_in_range(message.text, CAR_YEAR_MIN, CAR_YEAR_MAX, allow_separators=False)
    data = await state.get_data()
    y_min = int(data.get("year_min") or 0)
    if y is None or y < y_min:
        await message.answer("Yuqori yil pastki yildan kichik bo'lmasin va oralig'ta bo'lsin.")
        return
    await state.update_data(year_max=y)
    await state.set_state(WishlistStates.budget_max)
    await message.answer(
        "💵 <b>Maksimal byudjet — USD</b> (butun son, masalan <code>35000</code>):",
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(WishlistStates.budget_max), F.text)
async def wl_budget_max(message: Message, state: FSMContext) -> None:
    v = parse_int(message.text)
    if v is None:
        await message.answer("Byudjet: faqat musbat butun son (USD).")
        return
    if v < BUDGET_USD_MIN:
        await message.answer("Juda kichik. Masalan: 5000 ($5,000) yoki undan yuqori.")
        return
    if v > BUDGET_USD_MAX:
        await message.answer(f"Juda katta. Ko'pi bilan {BUDGET_USD_MAX:,} USD.")
        return
    await state.update_data(budget_max_usd=v)
    await state.set_state(WishlistStates.budget_min)
    await message.answer(
        "💵 <b>Minimal byudjet — USD</b> (butun son).\n"
        "Past chegara bo'lmasa — <code>0</code> yozing:",
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(WishlistStates.budget_min), F.text)
async def wl_budget_min(message: Message, state: FSMContext) -> None:
    v = parse_int(message.text)
    if v is None:
        await message.answer("0 yoki musbat butun son (USD).")
        return
    data = await state.get_data()
    mx = int(data.get("budget_max_usd") or 0)
    if v > 0 and v > mx:
        await message.answer("Minimal maksimaldan katta bo'lmasin.")
        return
    await state.update_data(budget_min_usd=None if v == 0 else v)
    await state.set_state(WishlistStates.condition)
    await message.answer("⚙️ Afzal ko'rgan holat:", reply_markup=_cond_kb())


@router.callback_query(StateFilter(WishlistStates.condition), F.data.startswith("wl_c:"))
async def wl_condition(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    if cq.from_user is None:
        await cq.answer()
        return
    key = (cq.data or "").split(":", 1)[1]
    cond = None if key == "_skip" else key
    if cond and cond not in {k for _, k in _COND_ROWS}:
        await cq.answer("Noto'g'ri", show_alert=True)
        return

    data = await state.get_data()
    await state.clear()

    client = await crm.get_or_create_client(
        telegram_id=cq.from_user.id,
        full_name=" ".join(filter(None, [cq.from_user.first_name, cq.from_user.last_name])) or None,
    )
    max_usd = int(data.get("budget_max_usd") or 0)
    bmin_usd = data.get("budget_min_usd")
    min_usd = int(bmin_usd) if bmin_usd is not None else None
    try:
        await crm.create_wishlist(
            client_id=client.id,
            brand=str(data.get("brand") or ""),
            model=data.get("model"),
            year_min=int(data.get("year_min") or 0),
            year_max=int(data.get("year_max") or 0),
            budget_min=min_usd,
            budget_max=max_usd,
            condition_key=cond,
        )
    except Exception:
        logging.exception("wishlist saqlash")
        await cq.answer("Saqlanmadi", show_alert=True)
        return

    if cq.message:
        await cq.message.edit_text(
            "🙏 <b>Rahmat!</b> Qidiruvingiz <b>saqlandi</b>.\n\n"
            "Mos keladigan e'lon kanalga chiqqanida sizga xabar beramiz.\n\n"
            "Pastdagi tugmalar orqali ro‘yxatni ko‘rishingiz yoki yangi qidiruv qo‘shishingiz mumkin — "
            "asosiy menyuga o‘tish shart emas.",
            parse_mode=ParseMode.HTML,
            reply_markup=_after_save_kb(),
        )
    await cq.answer("Saqlandi.", show_alert=False)


@router.callback_query(F.data.startswith("wl_y:"))
async def wl_interest_yes(cq: CallbackQuery, crm: CrmRepository) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    wid, lid = int(parts[1]), int(parts[2])
    wish = await crm.get_wishlist(wid)
    sub = await crm.get_listing_submission(lid)
    if wish is None or sub is None or sub.status != ListingSubmissionStatus.APPROVED:
        await cq.answer("Ma'lumot topilmadi", show_alert=True)
        return
    client = await crm.get_client_by_id(wish.client_id)
    if client is None or client.telegram_id != cq.from_user.id:
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return

    await cq.answer("Rahmat! Menejerga yuborildi.")

    ch = settings.channel_id
    pub = (settings.channel_username or "").strip().lstrip("@") or None
    mid = sub.channel_message_id
    photos = list(sub.photo_file_ids or [])

    sent_album = False
    if photos:
        try:
            cap = truncate_caption_html(listing_caption_from_sub_public(sub))
            media: list[InputMediaPhoto] = [
                InputMediaPhoto(media=photos[0], caption=cap, parse_mode="HTML"),
            ]
            media.extend(InputMediaPhoto(media=p) for p in photos[1:])
            await cq.bot.send_media_group(chat_id=cq.from_user.id, media=media)
            sent_album = True
        except TelegramBadRequest as e:
            logging.warning("Wishlist «Ko'rish»: albom yuborilmadi #%s: %s", lid, e)

    if not sent_album and mid is not None:
        try:
            await cq.bot.copy_message(
                chat_id=cq.from_user.id,
                from_chat_id=ch,
                message_id=mid,
            )
        except TelegramBadRequest as e:
            logging.warning("E'lon nusxalanmadi: %s", e)
            url = channel_post_url(
                channel_id=ch,
                message_id=mid,
                public_username=pub,
            )
            if url:
                await cq.bot.send_message(
                    cq.from_user.id,
                    f"📎 <a href=\"{html.escape(url)}\">E'lonni kanalda ko'rish</a>",
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )

    me = await cq.bot.get_me()
    bun = (me.username or "").strip().lstrip("@")
    ask_u = f"https://t.me/{bun}?start=lq_{lid}" if bun else None

    footer = msg.WISHLIST_LISTING_FOOTER.format(phone_links=sales_phones_links_html())
    reply_kb = (
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Savol yozish", url=ask_u)]],
        )
        if ask_u
        else None
    )
    try:
        await cq.bot.send_message(
            cq.from_user.id,
            footer,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_kb,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        pass

    phone = html.escape((client.phone or "—")[:32])
    name = html.escape((client.full_name or "Mijoz")[:120])
    adm = (
        f"🔔 <b>Wishlist — qiziqish</b>\n\n"
        f"Mijoz: {name}\n"
        f"🆔 Telegram: <code>{cq.from_user.id}</code>\n"
        f"📞 Telefon: <code>{phone}</code>\n\n"
        f"E'lon: <b>#{lid}</b> — {html.escape(sub.brand)} {html.escape(sub.model)}, {sub.year}\n"
        f"💰 {html.escape(fmt_usd(sub.price_ask_usd))}"
    )
    for aid in settings.admin_telegram_ids:
        try:
            await cq.bot.send_message(aid, adm, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            pass

    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("wl_l:"))
async def wl_interest_later(cq: CallbackQuery, crm: CrmRepository) -> None:
    if cq.from_user is None or cq.message is None:
        await cq.answer()
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await cq.answer()
        return
    wid = int(parts[1])
    wish = await crm.get_wishlist(wid)
    if wish is None:
        await cq.answer()
        return
    client = await crm.get_client_by_id(wish.client_id)
    if client is None or client.telegram_id != cq.from_user.id:
        await cq.answer("Ruxsat yo'q", show_alert=True)
        return
    await cq.answer("Yaxshi — boshqa mos e'lon chiqqanda yana xabar beramiz.")
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@router.message(StateFilter(WishlistStates))
async def wl_fallback(message: Message) -> None:
    await message.answer("Iltimos, bosqich bo'yicha matn yuboring yoki tugmalardan foydalaning.")
