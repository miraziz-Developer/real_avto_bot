"""E'lon berish: mashina ma'lumotlari (sotish oqimiga o'xshash), 3–8 rasm, admin moderatsiya, keyin kanal."""

from __future__ import annotations

import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.config import sales_phone_entries, settings
from bot.db.models import CarCondition
from bot.db.repositories import CrmRepository
from bot.data.car_catalog import (
    BRAND_MODELS as AD_BRANDS,
    BRAND_ORDER,
    BRANDS_PER_PAGE,
    GRID_COLS,
    MODELS_PER_PAGE,
    subvariants_for,
)
from bot.handlers.form_limits import CAR_YEAR_MAX, CAR_YEAR_MIN
from bot.handlers.render import present_root_menu
from bot.utils.contact_html import phone_link_html
from bot.utils.currency import fmt_usd

router = Router(name="ad_listing")

AD_CONDITIONS: dict[str, tuple[str, CarCondition]] = {
    "ideal": ("✨ Ideal", CarCondition.IDEAL),
    "yaxshi": ("👍 Yaxshi", CarCondition.YAXSHI),
    "qoniqarli": ("😐 Qoniqarli", CarCondition.QONIQARLI),
    "tamir": ("🔧 Ta'mir kerak", CarCondition.TAMIR),
}

# Kanal kapsioni «holati» qatori (emoji + qisqa nom)
_CONDITION_CAPTION: dict[str, tuple[str, str]] = {
    "ideal": ("✨", "Ideal"),
    "yaxshi": ("👍", "Yaxshi"),
    "qoniqarli": ("😐", "Qoniqarli"),
    "tamir": ("🔧", "Ta'mir kerak"),
}

MIN_PHOTOS = 3
MAX_PHOTOS = 8
EXTRA_MAX_LEN = 2000


class AdListingStates(StatesGroup):
    listing_brand = State()
    listing_model = State()
    listing_model_variant = State()
    listing_custom_model = State()
    listing_year = State()
    listing_location = State()
    listing_mileage = State()

    listing_condition = State()
    listing_accident = State()
    listing_price_usd = State()
    listing_paint = State()
    listing_extra = State()
    listing_photos = State()
    listing_phone = State()
    listing_payment = State()
    listing_confirm = State()


def _ad_brand_page_kb(page: int) -> InlineKeyboardMarkup:
    n = len(BRAND_ORDER)
    start = page * BRANDS_PER_PAGE
    chunk = BRAND_ORDER[start : start + BRANDS_PER_PAGE]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), GRID_COLS):
        row: list[InlineKeyboardButton] = []
        for j in range(GRID_COLS):
            k = i + j
            if k >= len(chunk):
                break
            bi = start + k
            label = chunk[k]
            row.append(InlineKeyboardButton(text=label, callback_data=f"ad_bi:{bi}"))
        rows.append(row)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ad_bpg:{page - 1}"))
    if start + BRANDS_PER_PAGE < n:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ad_bpg:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ad_model_kb_paged(brand: str, brand_idx: int, page: int) -> InlineKeyboardMarkup:
    models = AD_BRANDS.get(brand) or []
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
            row.append(InlineKeyboardButton(text=m, callback_data=f"ad_mi:{mi}"))
        rows.append(row)
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ad_mpg:{page - 1}"))
    if start + per < n:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ad_mpg:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="✍️ Boshqa model", callback_data="ad_model_other")])
    rows.append([InlineKeyboardButton(text="◀️ Markaga", callback_data="ad_bb")])
    rows.append([InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ad_variant_kb(variants: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(variants), GRID_COLS):
        row: list[InlineKeyboardButton] = []
        for j in range(GRID_COLS):
            vi = i + j
            if vi >= len(variants):
                break
            row.append(InlineKeyboardButton(text=variants[vi], callback_data=f"ad_mvi:{vi}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Modellarga", callback_data="ad_bm")])
    rows.append([InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _condition_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"ad_condition:{key}")]
            for key, (label, _) in AD_CONDITIONS.items()
        ]
        + [[InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")]]
    )


def _accident_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yo'q", callback_data="ad_accident:no")],
            [InlineKeyboardButton(text="⚠️ Ha", callback_data="ad_accident:yes")],
            [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")],
        ]
    )


def _paint_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Kraska toza", callback_data="ad_paint:clean")],
            [InlineKeyboardButton(text="🎨 Kraska bor", callback_data="ad_paint:painted")],
            [InlineKeyboardButton(text="❓ Bilmayman", callback_data="ad_paint:unknown")],
            [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")],
        ]
    )


def _photo_kb(count: int) -> InlineKeyboardMarkup:
    rows = []
    if count >= MIN_PHOTOS:
        rows.append([InlineKeyboardButton(text="✅ Rasmlar tayyor", callback_data="ad_photo_done")])
    rows.append([InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _extra_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Qo'shimchasiz davom etish", callback_data="ad_extra_skip")],
            [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")],
        ]
    )


def _phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Kontaktni yuborish (Telegram)", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yuborish (moderatsiya)", callback_data="ad_confirm_yes"),
                InlineKeyboardButton(text="❌ Bekor", callback_data="ad_confirm_no"),
            ],
            [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")],
        ]
    )


def truncate_caption_html(text: str, max_len: int = 1024) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def parse_phone_from_text(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) < 9:
        return None
    if s.startswith("+"):
        return f"+{digits}"
    return digits


def _valid_public_tg_username(raw: str | None) -> str | None:
    if not raw:
        return None
    u = raw.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", u):
        return None
    return u


def listing_album_caption_public(
    *,
    brand: str,
    model: str,
    year: int,
    mileage: int,
    location: str | None = None,
    condition_key: str,
    has_accident: bool,
    price_usd: int,
    paint_status: str,
    extra_details: str,
    contact_phones: list[str] | None = None,
    seller_phone: str | None = None,
    seller_tg_username: str | None = None,
) -> str:
    """Kanal va admin «jamoaga ko‘rinadigan» qism: Real Avto + sotuvchi aloqa raqamlari."""
    emo, cond_name = _CONDITION_CAPTION.get(condition_key, _CONDITION_CAPTION["yaxshi"])
    extra = (extra_details or "").strip()
    extra_block = ""
    if extra:
        extra_block = f"\n📝 <b>qo'shimcha:</b> {html.escape(extra[:1800])}"
    price_txt = fmt_usd(price_usd)
    phones = contact_phones if contact_phones is not None else sales_phone_entries()
    phone_lines = "\n".join(f"📞 {phone_link_html(p)}" for p in phones)
    # Sotuvchi aloqa qismi
    seller_contact_block = ""
    if seller_phone or seller_tg_username:
        seller_contact_block_lines: list[str] = ["<b>Sotuvchi bilan aloqa:</b>"]
        if seller_phone:
            seller_contact_block_lines.append(f"📞 {phone_link_html(seller_phone)}")
        if seller_tg_username:
            seller_contact_block_lines.append(f"💬 <a href='https://t.me/{html.escape(seller_tg_username)}'>@{html.escape(seller_tg_username)}</a>")
        seller_contact_block = "\n" + "\n".join(seller_contact_block_lines)
    loc_line = f"📍 <b>hudud:</b> {html.escape(location)}\n" if location else ""
    acc = "bor" if has_accident else "yo'q"
    disclaimer = (
        "\n\n"
        "⚠️ <b>Diqqat!</b>\n"
        "Kanal ma'muriyati mashinaning texnik holati va narxi uchun javobgar emas. "
        "Sotib olishdan oldin albatta ustangizga (diagnostika) ko'rsating yoki Real Avtoga murojat qiling. "
        "Hech qachon mashinani ko'rmasdan oldindan zaklad o'tkazmang!"
    )
    return (
        "🚘 <b>REAL AVTO</b> · <b>E'LON</b>\n\n"
        f"<b>{html.escape(brand)} {html.escape(model)}</b> · <code>{year}</code>\n"
        f"{loc_line}"
        f"🛣 <b>yurgani:</b> <code>{mileage:,}</code> km ·\n"
        f"{emo} <b>holati:</b> {html.escape(cond_name)} · <b>avariya</b> {acc}\n"
        f"🎨 {html.escape(paint_status)}\n"
        f"💰 <b>{html.escape(price_txt)}</b>"
        f"{extra_block}\n\n"
        "<b>Aloqa uchun:</b>\n"
        f"{phone_lines}"
        f"{seller_contact_block}"
        f"{disclaimer}"
    )



def listing_album_caption_moderation(
    lid: int,
    tg_id: int,
    *,
    brand: str,
    model: str,
    year: int,
    mileage: int,
    location: str | None = None,
    condition_key: str,
    has_accident: bool,
    price_usd: int,
    paint_status: str,
    phone: str,
    client_name: str,
    extra_details: str,
    seller_username: str | None = None,
) -> str:
    sales = settings.sales_phone
    un = _valid_public_tg_username(seller_username)
    un_line = f"\n🔗 @{html.escape(un)}" if un else ""
    pre = (
        f"📢 <b>Moderatsiya #{lid}</b>\n🆔 <code>{tg_id}</code>\n"
        f"👤 <b>Mijoz</b> (ichki): {html.escape((client_name or '').strip() or '—')}{un_line}\n"
        f"🔐 <b>Sotuvchi tel</b> (ichki): <code>{html.escape(phone)}</code>\n"
        f"📢 <b>Kanalda chiqadigan aloqa</b>: <code>{html.escape(sales)}</code>\n"
    )
    body = listing_album_caption_public(
        brand=brand,
        model=model,
        year=year,
        mileage=mileage,
        location=location,
        condition_key=condition_key,
        has_accident=has_accident,
        price_usd=price_usd,
        paint_status=paint_status,
        extra_details=extra_details,
        contact_phones=sales_phone_entries(),
    )
    return pre + body


def listing_caption_from_sub_public(sub) -> str:
    """Kanal uchun: Real Avto + sotuvchi aloqa raqamlari chiqadi."""
    return listing_album_caption_public(
        brand=sub.brand,
        model=sub.model,
        year=sub.year,
        mileage=sub.mileage,
        location=getattr(sub, "location", None) or None,
        condition_key=sub.condition_key,
        has_accident=sub.has_accident,
        price_usd=sub.price_ask_usd,
        paint_status=sub.paint_status,
        extra_details=getattr(sub, "extra_details", None) or "",
        contact_phones=sales_phone_entries(),
        seller_phone=sub.phone or None,
        seller_tg_username=sub.seller_username or None,
    )


async def send_admin_listing_album_with_actions(
    bot: Bot,
    admin_chat_id: int,
    *,
    lid: int,
    tg_id: int,
    client_name: str,
    brand: str,
    model: str,
    year: int,
    mileage: int,
    location: str | None = None,
    condition_key: str,
    has_accident: bool,
    price_ask_usd: int,
    paint_status: str,
    phone: str,
    extra_details: str,
    seller_username: str | None,
    photo_file_ids: list[str],
    mod_kb: InlineKeyboardMarkup,
    payment_screenshot_file_id: str | None = None,
) -> None:
    cap = listing_album_caption_moderation(
        lid,
        tg_id,
        brand=brand,
        model=model,
        year=year,
        mileage=mileage,
        location=location,
        condition_key=condition_key,
        has_accident=has_accident,
        price_usd=price_ask_usd,
        paint_status=paint_status,
        phone=phone,
        client_name=client_name,
        extra_details=extra_details,
        seller_username=seller_username,
    )
    cap = truncate_caption_html(cap)
    media = [InputMediaPhoto(media=photo_file_ids[0], caption=cap, parse_mode="HTML")]
    media.extend(InputMediaPhoto(media=p) for p in photo_file_ids[1:])
    msgs = await bot.send_media_group(admin_chat_id, media)
    # To'lov skrinshoti adminlarga
    if payment_screenshot_file_id:
        await bot.send_photo(
            admin_chat_id,
            photo=payment_screenshot_file_id,
            caption=f"💳 E'lon <b>#{lid}</b> to'lov skrinshoti.",
            reply_to_message_id=msgs[0].message_id,
            parse_mode=ParseMode.HTML,
        )
    await bot.send_message(
        admin_chat_id,
        f"🛎 E'lon <b>#{lid}</b> — yuqoridagi to'plam bo'yicha tasdiqlang yoki rad eting.",
        reply_markup=mod_kb,
        reply_to_message_id=msgs[0].message_id,
        parse_mode=ParseMode.HTML,
    )


def _summary_text(data: dict) -> str:
    brand = str(data.get("brand") or "")
    model = str(data.get("model") or "")
    year = int(data.get("year") or 0)
    mileage = int(data.get("mileage") or 0)
    location = str(data.get("location") or "").strip()
    ck = str(data.get("condition_key") or "yaxshi")
    _, cond = AD_CONDITIONS.get(ck, AD_CONDITIONS["yaxshi"])
    has_acc = bool(data.get("has_accident"))
    price_usd = int(data.get("price_ask") or 0)
    paint = str(data.get("paint_status") or "")
    extra = str(data.get("extra_details") or "").strip()
    phone = str(data.get("phone") or "")
    photos = list(data.get("photos") or [])
    extra_line = (
        f"\n📝 Qo'shimcha: <i>{html.escape(extra[:400])}{'…' if len(extra) > 400 else ''}</i>\n"
        if extra
        else ""
    )
    pu = _valid_public_tg_username(str(data.get("preview_username") or ""))
    tg_line = f"\n🔹 Telegram: <a href=\"https://t.me/{html.escape(pu)}\">@{html.escape(pu)}</a>\n" if pu else ""
    pub_lines = "\n".join(html.escape(p) for p in sales_phone_entries())
    return (
        "📋 <b>E'lon xulosasi</b>\n\n"
        f"🚘 {html.escape(brand)} {html.escape(model)}, <b>{year}</b> yil\n"
        f"📍 Hudud: <b>{html.escape(location) if location else '—'}</b>\n"
        f"🛣 Yurish: <b>{mileage:,}</b> km\n"
        f"⚙️ Holat: <b>{html.escape(cond.value)}</b>\n"
        f"⚠️ Avariya: <b>{'Ha' if has_acc else 'Yo`q'}</b>\n"
        f"🎨 Kraska: <b>{html.escape(paint)}</b>\n"
        f"💵 So'ralayotgan narx (USD): <b>{fmt_usd(price_usd)}</b>\n"
        f"{extra_line}"
        f"📸 Rasmlar: <b>{len(photos)}</b> ta\n"
        f"📞 <b>Siz kiritgan aloqa</b> (faqat moderatsiya): <b>{html.escape(phone)}</b>\n"
        f"📢 <b>Kanalda chiqadigan aloqa</b> (bir yoki bir nechta raqam):\n<b>{pub_lines}</b> <i>(Real Avto)</i>"
        f"{tg_line}\n\n"
        "<i>Xaridorlar shu raqam orqali bog‘lanadi; savdo Real Avto orqali.</i>\n\n"
        "Hammasi to'g'rimi? Moderatsiyadan o'tgach kanalga chiqadi."
    )


@router.callback_query(F.data == "ad_start")
async def ad_start(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdListingStates.listing_brand)
    await state.set_data({"brand_list_page": 0})
    if cq.message:
        await cq.message.edit_text(
            "📢 <b>E'lon berish</b>\n\n"
            "Markani tanlang — <b>3 ustun</b>, bir sahifada 9 ta, ortiqchasi ◀️ ▶️ sahifalarda.",
            reply_markup=_ad_brand_page_kb(0),
            parse_mode=ParseMode.HTML,
        )
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_brand), F.data.startswith("ad_bpg:"))
async def ad_brand_page_nav(cq: CallbackQuery, state: FSMContext) -> None:
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
    await cq.message.edit_reply_markup(reply_markup=_ad_brand_page_kb(page))
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_brand), F.data.startswith("ad_bi:"))
async def ad_brand_pick_idx(cq: CallbackQuery, state: FSMContext) -> None:
    tail = (cq.data or "").split(":", 1)[-1]
    if not tail.isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    bi = int(tail)
    if bi < 0 or bi >= len(BRAND_ORDER):
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    brand = BRAND_ORDER[bi]
    await state.update_data(brand=brand, brand_idx=bi, model_page=0)
    if brand == "Boshqa":
        await state.set_state(AdListingStates.listing_custom_model)
        if cq.message:
            await cq.message.edit_text("✍️ Modelni yozing:")
    else:
        await state.set_state(AdListingStates.listing_model)
        if cq.message:
            await cq.message.edit_text(
                f"🚘 <b>{html.escape(brand)}</b> — modelni tanlang (3 ustun, sahifalar ◀️ ▶️):",
                parse_mode=ParseMode.HTML,
                reply_markup=_ad_model_kb_paged(brand, bi, 0),
            )
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_model), F.data.startswith("ad_mpg:"))
async def ad_model_page_nav(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    raw = (cq.data or "").split(":", 1)[-1]
    if not raw.lstrip("-").isdigit():
        await cq.answer()
        return
    page = int(raw)
    data = await state.get_data()
    brand = str(data.get("brand") or "")
    bi = int(data.get("brand_idx") or 0)
    models = AD_BRANDS.get(brand) or []
    if not models:
        await cq.answer()
        return
    max_p = max(0, (len(models) - 1) // MODELS_PER_PAGE)
    page = max(0, min(page, max_p))
    await state.update_data(model_page=page)
    await cq.message.edit_reply_markup(reply_markup=_ad_model_kb_paged(brand, bi, page))
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_model), F.data == "ad_bb")
async def ad_back_to_brands(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    data = await state.get_data()
    page = int(data.get("brand_list_page") or 0)
    max_p = max(0, (len(BRAND_ORDER) - 1) // BRANDS_PER_PAGE)
    page = max(0, min(page, max_p))
    await state.set_state(AdListingStates.listing_brand)
    await cq.message.edit_text(
        "📢 <b>E'lon berish</b>\n\n"
        "Markani tanlang — <b>3 ustun</b>, bir sahifada 9 ta, ortiqchasi ◀️ ▶️ sahifalarda.",
        parse_mode=ParseMode.HTML,
        reply_markup=_ad_brand_page_kb(page),
    )
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_model), F.data.startswith("ad_mi:"))
async def ad_model_pick_idx(cq: CallbackQuery, state: FSMContext) -> None:
    tail = (cq.data or "").split(":", 1)[-1]
    if not tail.isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    mi = int(tail)
    data = await state.get_data()
    brand = str(data.get("brand") or "")
    models = AD_BRANDS.get(brand) or []
    if mi < 0 or mi >= len(models):
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    model = models[mi]
    subs = subvariants_for(brand, model)
    if subs and cq.message:
        await state.update_data(pending_base_model=model, variant_options=subs)
        await state.set_state(AdListingStates.listing_model_variant)
        await cq.message.edit_text(
            f"🚘 <b>{html.escape(brand)} {html.escape(model)}</b> — aniq turini tanlang:",
            parse_mode=ParseMode.HTML,
            reply_markup=_ad_variant_kb(subs),
        )
        await cq.answer()
        return
    await state.update_data(model=model, variant_options=None, pending_base_model=None)
    await state.set_state(AdListingStates.listing_year)
    if cq.message:
        await cq.message.edit_text(f"📅 Yilini kiriting ({CAR_YEAR_MIN}-{CAR_YEAR_MAX}):")
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_model_variant), F.data.startswith("ad_mvi:"))
async def ad_variant_pick(cq: CallbackQuery, state: FSMContext) -> None:
    tail = (cq.data or "").split(":", 1)[-1]
    if not tail.isdigit():
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    vi = int(tail)
    data = await state.get_data()
    opts = list(data.get("variant_options") or [])
    if vi < 0 or vi >= len(opts):
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    await state.update_data(model=opts[vi], variant_options=None, pending_base_model=None)
    await state.set_state(AdListingStates.listing_year)
    if cq.message:
        await cq.message.edit_text(f"📅 Yilini kiriting ({CAR_YEAR_MIN}-{CAR_YEAR_MAX}):")
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_model_variant), F.data == "ad_bm")
async def ad_variant_back_models(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.message is None:
        await cq.answer()
        return
    data = await state.get_data()
    brand = str(data.get("brand") or "")
    bi = int(data.get("brand_idx") or 0)
    page = int(data.get("model_page") or 0)
    await state.set_state(AdListingStates.listing_model)
    await state.update_data(variant_options=None, pending_base_model=None)
    await cq.message.edit_text(
        f"🚘 <b>{html.escape(brand)}</b> — modelni tanlang (3 ustun, sahifalar ◀️ ▶️):",
        parse_mode=ParseMode.HTML,
        reply_markup=_ad_model_kb_paged(brand, bi, page),
    )
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_model), F.data == "ad_model_other")
async def ad_model_other(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdListingStates.listing_custom_model)
    if cq.message:
        await cq.message.edit_text("✍️ Modelni yozing:")
    await cq.answer()


@router.message(StateFilter(AdListingStates.listing_custom_model), F.text)
async def ad_custom_model(message: Message, state: FSMContext) -> None:
    model = (message.text or "").strip()
    if len(model) < 2:
        await message.answer("Model nomi juda qisqa.")
        return
    await state.update_data(model=model)
    await state.set_state(AdListingStates.listing_year)
    await message.answer(f"📅 Yilini kiriting ({CAR_YEAR_MIN}-{CAR_YEAR_MAX}):")


@router.message(StateFilter(AdListingStates.listing_year), F.text)
async def ad_year(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Yil raqam bo'lishi kerak.")
        return
    year = int(raw)
    if year < CAR_YEAR_MIN or year > CAR_YEAR_MAX:
        await message.answer(f"Yil {CAR_YEAR_MIN}–{CAR_YEAR_MAX} oralig'ida bo'lsin.")
        return
    await state.update_data(year=year)
    await state.set_state(AdListingStates.listing_location)
    await message.answer("📍 Mashina joylashgan hududni kiriting (masalan: Toshkent, Samarqand):")


@router.message(StateFilter(AdListingStates.listing_location), F.text)
async def ad_location(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    # oddiy validatsiya — minimal uzunlik va maksimal uzunlik
    if len(raw) < 2:
        await message.answer("Hudud nomi juda qisqa.")
        return
    if len(raw) > 120:
        await message.answer("Hudud nomi 120 belgidan uzun bo'lmasligi kerak.")
        return
    await state.update_data(location=raw)
    await state.set_state(AdListingStates.listing_mileage)
    await message.answer("🛣 Yurish masofasi (km):")


@router.message(StateFilter(AdListingStates.listing_mileage), F.text)
async def ad_mileage(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(" ", "").replace(".", "").replace(",", "")
    if not raw.isdigit():
        await message.answer("Yurish raqam bo'lishi kerak.")
        return
    mileage = int(raw)
    await state.update_data(mileage=mileage)
    await state.set_state(AdListingStates.listing_condition)
    await message.answer("⚙️ Mashina holatini tanlang:", reply_markup=_condition_kb())


@router.callback_query(StateFilter(AdListingStates.listing_condition), F.data.startswith("ad_condition:"))
async def ad_condition(cq: CallbackQuery, state: FSMContext) -> None:
    key = (cq.data or "").split(":", 1)[1]
    if key not in AD_CONDITIONS:
        await cq.answer("Noto'g'ri", show_alert=True)
        return
    await state.update_data(condition_key=key)
    await state.set_state(AdListingStates.listing_accident)
    if cq.message:
        await cq.message.edit_text("⚠️ Avariya (DTP) bo'lganmi?", reply_markup=_accident_kb())
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_accident), F.data.startswith("ad_accident:"))
async def ad_accident(cq: CallbackQuery, state: FSMContext) -> None:
    is_accident = (cq.data or "").endswith(":yes")
    await state.update_data(has_accident=is_accident)
    await state.set_state(AdListingStates.listing_price_usd)
    if cq.message:
        await cq.message.edit_text("💵 So'ralayotgan narxni kiriting (USD, butun son):")
    await cq.answer()


@router.message(StateFilter(AdListingStates.listing_price_usd), F.text)
async def ad_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("Narx raqam bo'lishi kerak.")
        return
    await state.update_data(price_ask=int(raw))
    await state.set_state(AdListingStates.listing_paint)
    await message.answer("🎨 Kraska holatini tanlang:", reply_markup=_paint_kb())


@router.callback_query(StateFilter(AdListingStates.listing_paint), F.data.startswith("ad_paint:"))
async def ad_paint(cq: CallbackQuery, state: FSMContext) -> None:
    key = (cq.data or "").split(":", 1)[1]
    label = {"clean": "Kraska toza", "painted": "Kraska bor", "unknown": "Noma'lum"}.get(key, "Noma'lum")
    await state.update_data(paint_status=label, photos=[], extra_details="")
    await state.set_state(AdListingStates.listing_extra)
    if cq.message:
        await cq.message.edit_text(
            "📝 <b>Qo'shimcha tavsiflar</b> (ixtiyoriy)\n\n"
            "Masalan: mayda chiziq-jarohatlar, bitta joyda ranga tegish, "
            "nosozliklar, zaxira kalitkasi, shinalar, qo'shimcha jihozlar va hokazo.\n\n"
            "Bitta xabar bilan yozing yoki «Qo'shimchasiz davom etish»ni bosing.",
            reply_markup=_extra_kb(),
            parse_mode=ParseMode.HTML,
        )
    await cq.answer()


async def _show_photo_collect_ui(message: Message, *, use_edit: CallbackQuery | None = None) -> None:
    text = (
        f"📸 Mashina rasmlarini yuboring (faqat rasm).\n\n"
        f"Kamida <b>{MIN_PHOTOS}</b> ta, ko'pi bilan <b>{MAX_PHOTOS}</b> ta.\n"
        "Tugagach «Rasmlar tayyor» tugmasini bosing."
    )
    if use_edit is not None and use_edit.message:
        await use_edit.message.edit_text(text, reply_markup=_photo_kb(0), parse_mode=ParseMode.HTML)
    else:
        await message.answer(text, reply_markup=_photo_kb(0), parse_mode=ParseMode.HTML)


@router.callback_query(StateFilter(AdListingStates.listing_extra), F.data == "ad_extra_skip")
async def ad_extra_skip(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(extra_details="")
    await state.set_state(AdListingStates.listing_photos)
    if cq.message:
        await _show_photo_collect_ui(cq.message, use_edit=cq)
    await cq.answer()


@router.message(StateFilter(AdListingStates.listing_extra), F.text)
async def ad_extra_text(message: Message, state: FSMContext) -> None:
    t = (message.text or "").strip()
    if len(t) > EXTRA_MAX_LEN:
        await message.answer(f"Maksimum {EXTRA_MAX_LEN} belgi.", reply_markup=_extra_kb())
        return
    await state.update_data(extra_details=t)
    await state.set_state(AdListingStates.listing_photos)
    await _show_photo_collect_ui(message)


@router.message(StateFilter(AdListingStates.listing_extra))
async def ad_extra_fallback(message: Message) -> None:
    await message.answer(
        "Matn yozing yoki «Qo'shimchasiz davom etish» tugmasini bosing.",
        reply_markup=_extra_kb(),
    )


@router.message(StateFilter(AdListingStates.listing_photos), F.photo)
async def ad_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[str] = list(data.get("photos") or [])
    if len(photos) >= MAX_PHOTOS:
        await message.answer(f"Maksimum {MAX_PHOTOS} ta rasm.")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(
        f"✅ Rasm qabul qilindi ({len(photos)}/{MAX_PHOTOS}). "
        f"Kamida {MIN_PHOTOS} ta bo'lgach «Rasmlar tayyor»ni bosing.",
        reply_markup=_photo_kb(len(photos)),
    )


@router.callback_query(StateFilter(AdListingStates.listing_photos), F.data == "ad_photo_done")
async def ad_photo_done(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    photos = list(data.get("photos") or [])
    if len(photos) < MIN_PHOTOS:
        await cq.answer(f"Kamida {MIN_PHOTOS} ta rasm kerak", show_alert=True)
        return
    await state.set_state(AdListingStates.listing_phone)
    if cq.message:
        await cq.message.edit_text(
            "📱 <b>Siz bilan aloqa (moderatsiya uchun)</b>\n\n"
            "Kanalda va xaridorlar uchun <b>Real Avto raqami</b> chiqadi — shaxsiy raqamingiz e’londa ko‘rinmaydi.\n"
            "Quyidagi raqam faqat jamoamiz siz bilan bog‘lanishi uchun kerak.\n\n"
            "Pastdagi tugma orqali kontak yuboring <b>yoki</b> raqamni yozing "
            "(masalan: <code>+998901234567</code>).",
            parse_mode=ParseMode.HTML,
        )
        await cq.message.answer("Kontakt yoki raqam:", reply_markup=_phone_kb())
    await cq.answer()


@router.message(StateFilter(AdListingStates.listing_photos))
async def ad_photo_fallback(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    n = len(list(data.get("photos") or []))
    await message.answer(
        f"Faqat rasm yuboring (kamida {MIN_PHOTOS}, ko'pi bilan {MAX_PHOTOS} ta). Video yuborilmaydi.",
        reply_markup=_photo_kb(n),
    )


async def _go_next_after_phone(message: Message, state: FSMContext) -> None:
    """Telefondan keyin: pullik rejimda to'lov bosqichi, aks holda darhol xulosa."""
    if settings.listing_payment_enabled:
        await _go_payment_after_phone(message, state)
    else:
        await _go_confirm_after_payment(message, state)


async def _go_payment_after_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(AdListingStates.listing_payment)
    lines = [
        f"💳 <b>E'lon uchun to'lov: {settings.listing_price_uzs:,} so'm</b>",
        "",
        f"💳 Karta: <code>{html.escape(settings.payment_card)}</code>",
        f"👤 Egasi: {html.escape(settings.payment_card_holder)}",
        "",
        "To'lovni amalga oshirib, skrinshot yuboring (rasm sifatida).",
    ]
    await message.answer("💳 To'lov ma'lumotlari:", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )



async def _go_confirm_after_payment(message: Message, state: FSMContext) -> None:
    await state.set_state(AdListingStates.listing_confirm)
    data = await state.get_data()
    await message.answer("📋 E'lon xulosasi:", reply_markup=ReplyKeyboardRemove())
    await message.answer(_summary_text(data), reply_markup=_confirm_kb(), parse_mode=ParseMode.HTML)



@router.message(StateFilter(AdListingStates.listing_phone), F.contact)
async def ad_phone_contact(message: Message, state: FSMContext) -> None:
    if message.contact is None or message.from_user is None:
        return
    phone = message.contact.phone_number
    un = message.from_user.username or ""
    await state.update_data(phone=phone, preview_username=un)
    await _go_next_after_phone(message, state)



@router.message(StateFilter(AdListingStates.listing_phone), F.text)
async def ad_phone_text(message: Message, state: FSMContext) -> None:
    parsed = parse_phone_from_text(message.text or "")
    if not parsed:
        await message.answer(
            "Raqamni tushunarli qilib yozing (kamida 9 ta raqam), yoki pastdagi tugmadan kontakt yuboring.",
            reply_markup=_phone_kb(),
        )
        return
    un = (message.from_user.username or "") if message.from_user else ""
    await state.update_data(phone=parsed, preview_username=un)
    await _go_next_after_phone(message, state)



@router.message(StateFilter(AdListingStates.listing_phone))
async def ad_phone_fallback_msg(message: Message) -> None:
    await message.answer("Kontakt yoki telefon raqamini matn bilan yuboring.", reply_markup=_phone_kb())


@router.message(StateFilter(AdListingStates.listing_payment), F.photo)
async def ad_payment_screenshot(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(payment_screenshot_file_id=file_id)
    await _go_confirm_after_payment(message, state)


@router.message(StateFilter(AdListingStates.listing_payment))
async def ad_payment_fallback(message: Message) -> None:
    await message.answer(
        "Iltimos, to'lov skrinshotini rasm sifatida yuboring.",
    )




@router.callback_query(StateFilter(AdListingStates.listing_confirm), F.data == "ad_confirm_no")
async def ad_confirm_no(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if cq.message:
        await present_root_menu(callback=cq)
    await cq.answer()


@router.callback_query(StateFilter(AdListingStates.listing_confirm), F.data == "ad_confirm_yes")
async def ad_confirm_yes(cq: CallbackQuery, state: FSMContext, crm: CrmRepository) -> None:
    if cq.from_user is None:
        await cq.answer()
        return
    data = await state.get_data()
    brand = str(data.get("brand") or "")
    model = str(data.get("model") or "")
    year = int(data.get("year") or 0)
    mileage = int(data.get("mileage") or 0)
    condition_key = str(data.get("condition_key") or "yaxshi")
    has_accident = bool(data.get("has_accident"))
    price_ask = int(data.get("price_ask") or 0)
    paint_status = str(data.get("paint_status") or "")
    extra_details = str(data.get("extra_details") or "")
    photos = list(data.get("photos") or [])
    phone = str(data.get("phone") or "")
    location = str(data.get("location") or "").strip()
    payment_screenshot_file_id = data.get("payment_screenshot_file_id")
    if not location or len(location) < 2:
        await cq.answer("Hudud ma'lumotlari yetarli emas. Iltimos, qayta yuboring.", show_alert=True)
        return
    if len(photos) < MIN_PHOTOS or len(photos) > MAX_PHOTOS:
        await cq.answer("Rasm soni noto'g'ri", show_alert=True)
        return

    # Tugma "yuklanmoqda" holatini yopish — adminlarga media yuborish uzoq davom etishi mumkin.
    await cq.answer()


    try:
        client = await crm.get_or_create_client(
            telegram_id=cq.from_user.id,
            full_name=" ".join(filter(None, [cq.from_user.first_name, cq.from_user.last_name])) or None,
            phone=phone,
        )
        row = await crm.create_listing_submission(
            client_id=client.id,
            user_telegram_id=cq.from_user.id,
            seller_username=cq.from_user.username,
            brand=brand,
            model=model,
            year=year,
            mileage=mileage,
            condition_key=condition_key,
            has_accident=has_accident,
            price_ask_usd=price_ask,
            paint_status=paint_status,
            extra_details=extra_details,
            location=location or None,
            phone=phone,
            photo_file_ids=photos,
            payment_screenshot_file_id=payment_screenshot_file_id,
        )

        lid = row.id
    except Exception:
        logging.exception("ad_confirm_yes: DB yoki saqlash")
        if cq.message:
            await cq.message.reply("❌ Saqlanmadi (baza yoki tarmoq). Qayta urinib ko'ring.")
        return

    mod_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"lad_a:{lid}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"lad_r:{lid}"),
            ]
        ]
    )

    cname = client.full_name or "Mijoz"
    seller_un = cq.from_user.username
    if settings.admin_telegram_ids:
        for aid in settings.admin_telegram_ids:
            try:
                await send_admin_listing_album_with_actions(
                    cq.bot,
                    aid,
                    lid=lid,
                    tg_id=cq.from_user.id,
                    client_name=cname,
                    brand=brand,
                    model=model,
                    year=year,
                    mileage=mileage,
                    location=location or None,
                    condition_key=condition_key,
                    has_accident=has_accident,
                    price_ask_usd=price_ask,
                    paint_status=paint_status,
                    phone=phone,
                    extra_details=extra_details,
                    seller_username=seller_un,
                    photo_file_ids=photos,
                    mod_kb=mod_kb,
                    payment_screenshot_file_id=payment_screenshot_file_id,
                )

            except Exception:
                logging.exception("Admin #%s ga e'lon yuborish muvaffaqiyatsiz", aid)
                continue
    else:
        logging.warning("ADMIN_TELEGRAM_IDS bo'sh — e'lon #%s adminlarga yuborilmadi", lid)

    await state.clear()
    if cq.message:
        await cq.message.answer(
            "✅ E'loningiz moderatsiyaga yuborildi.\n\n"
            "Admin tekshirgach, tasdiqlansa kanalda chiqadi; rad etilsa sabab bilan xabar beramiz.\n\n"
            "Rad etilganda «Elon berish»dan qayta yuborishingiz mumkin.",
            parse_mode=ParseMode.HTML,
        )
        await present_root_menu(callback=cq)


@router.message(StateFilter(AdListingStates))
async def ad_fallback(message: Message) -> None:
    await message.answer("Iltimos, bosqich bo'yicha javob bering yoki Asosiy menyuga qayting.")
