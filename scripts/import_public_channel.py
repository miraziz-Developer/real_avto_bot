"""Kanal tarixini AI'siz import qilish: ochiq veb ko'rinishdan (t.me/s/<kanal>) o'qiydi.

Bot konteyneri ichida ishga tushiriladi (bazaga ulanish va bot kodi shu yerda):
    docker compose exec -T bot python - [--apply] [--days 60] < scripts/import_public_channel.py

Kanal formati (@REAL_AVTO_ARZON): 1–2 dumaloq video + «🚘AVTOMOBIL: … / yili / probeg / 📞Aloqa» posti.
Sotilgani: post tahrirlanib «SOTILDI» qo'shiladi yoki telefon olib tashlanadi; yoki alohida «SPARK BARAKA BO'LDI».
Veb ko'rinish postning hozirgi (tahrirlangan) matnini ko'rsatadi.

--apply bo'lmasa — faqat hisobot (bazaga hech narsa yozilmaydi). Dumaloq videolar eshitilmaydi (AI sarflanmaydi);
ularning post ID lari mashinaga bog'lanadi, shuning uchun keyingi tahrir/reply'lar ham to'g'ri topiladi.
"""

from __future__ import annotations

import asyncio
import html
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from bot.config import settings
from bot.db import base as db_base
from bot.db.cars_repo import CarRepository, same_car_model
from bot.db.models import CarSource, CarStatus
from bot.services.car_parser import has_phone, is_sold_text, parse_car_text

APPLY = "--apply" in sys.argv
DAYS = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 60
MAX_PAGES = 80
RV_GAP = timedelta(minutes=5)
ANNOUNCE_WINDOW = timedelta(days=10)
# Kanal sotilmagan mashinani muntazam qayta tashlaydi; shundan eski, qayta chiqmagan e'lon — eskirgan (arxiv)
# Egasining tasdig'i: shu postlardagi mashinalar sotilgan («--sold 5107,5129»)
SOLD_POSTS = {int(x) for x in sys.argv[sys.argv.index("--sold") + 1].split(",") if x.strip()} if "--sold" in sys.argv else set()
FRESH_DAYS = int(sys.argv[sys.argv.index("--fresh") + 1]) if "--fresh" in sys.argv else 14


@dataclass
class Post:
    id: int
    date: datetime
    text: str
    round_video: bool
    forwarded: bool


@dataclass
class Listing:
    post: Post
    parsed: object
    sold: bool
    reason: str = ""
    stale: bool = False
    message_ids: list[int] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    # «SPARK BARAKA BO'LDI» kabi postlar shu mashinaga tegishli bo'lishi mumkin — admin tasdiqlaydi
    doubts: list[str] = field(default_factory=list)


def fetch_posts(username: str) -> list[Post]:
    posts: dict[int, Post] = {}
    before = None
    oldest_wanted = datetime.now(UTC) - timedelta(days=DAYS)
    for _ in range(MAX_PAGES):
        url = f"https://t.me/s/{username}" + (f"?before={before}" if before else "")
        blocks: list[str] = []
        for attempt in range(4):
            # Telegram tez so'rovlarga vaqtincha bo'sh sahifa qaytaradi — kutib qayta urinamiz
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            except OSError:
                page = ""
            blocks = page.split('class="tgme_widget_message_wrap')[1:]
            if blocks:
                break
            time.sleep(3 * (attempt + 1))
        time.sleep(1)
        ids = []
        for b in blocks:
            m = re.search(rf'data-post="{re.escape(username)}/(\d+)"', b, re.IGNORECASE)
            dt = re.search(r'<time datetime="([^"]+)"', b)
            if not m or not dt:
                continue
            pid = int(m.group(1))
            ids.append(pid)
            t = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
            text = ""
            if t:
                text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", t.group(1)))).strip()
            posts[pid] = Post(
                id=pid,
                date=datetime.fromisoformat(dt.group(1)),
                text=text,
                round_video="tgme_widget_message_roundvideo" in b,
                forwarded="tgme_widget_message_forwarded_from" in b,
            )
        if not ids:
            break
        before = min(ids)
        if posts[before].date < oldest_wanted:
            break
    return sorted((p for p in posts.values() if p.date >= oldest_wanted), key=lambda p: p.id)


def is_listing(p: Post) -> bool:
    return bool(re.search(r"avtomobil\s*:", p.text, re.IGNORECASE)) or bool(re.search(r"yili\s*:", p.text, re.IGNORECASE))


def model_key(parsed) -> tuple:
    return ((parsed.model or parsed.brand or "").split()[0].lower(), parsed.year, parsed.mileage_km)


def analyse(posts: list[Post]) -> tuple[list[Listing], list[str]]:
    listings: list[Listing] = []
    notes: list[str] = []
    by_id = {p.id: i for i, p in enumerate(posts)}
    for p in posts:
        if not p.text or not is_listing(p):
            continue
        parsed = parse_car_text(p.text, usd_rate_uzs=settings.usd_rate_uzs)
        if not (parsed.model or parsed.brand) or not parsed.looks_like_car():
            notes.append(f"• {p.id}: e'lon emas / model topilmadi — «{p.text[:60]!r}»")
            continue
        sold = is_sold_text(p.text) or not has_phone(p.text)
        reason = "postda SOTILDI" if is_sold_text(p.text) else ("telefon olib tashlangan" if sold else "")
        ids = [p.id]
        i = by_id[p.id] - 1
        while i >= 0 and posts[i].round_video and not posts[i].forwarded and p.date - posts[i].date <= RV_GAP:
            ids.append(posts[i].id)
            i -= 1
        listings.append(Listing(p, parsed, sold, reason, message_ids=ids, texts={str(p.id): p.text}))

    # Qayta tashlangan e'lonlar (bir xil model + yil + probeg) — bitta mashina, eng oxirgi post holati
    merged: dict[tuple, Listing] = {}
    for item in listings:
        key = model_key(item.parsed)
        prev = merged.get(key) if key[2] else None
        if prev is not None:
            item.message_ids = [*item.message_ids, *prev.message_ids]
            item.texts = {**prev.texts, **item.texts}
        merged[key if key[2] else (key, item.post.id)] = item
    unique = sorted(merged.values(), key=lambda x: x.post.id)

    # «SPARK BARAKA BO'LDI» — alohida post: oldingi 10 kundagi sotuvdagi yagona mos e'lon sotildi
    for p in posts:
        if not p.text or is_listing(p) or not is_sold_text(p.text):
            continue
        ann = parse_car_text(p.text, usd_rate_uzs=settings.usd_rate_uzs)
        if not ann.model:
            notes.append(f"• {p.id}: «{p.text[:40]}» — model yo'q, hech narsa belgilanmadi")
            continue
        cands = [
            x for x in unique
            if not x.sold and x.parsed.model and same_car_model(x.parsed.model, ann.model)
            and (not ann.year or x.parsed.year == ann.year)
            and timedelta(0) <= p.date - x.post.date <= ANNOUNCE_WINDOW
        ]
        if len(cands) == 1:
            cands[0].sold = True
            cands[0].reason = f"«{p.text.splitlines()[0][:30]}» posti ({p.id})"
            cands[0].message_ids.append(p.id)
        elif len(cands) > 1:
            for c in cands:
                c.doubts.append(f"«{p.text.splitlines()[0][:40]}» (t.me/{{username}}/{p.id})")
            names = ", ".join(f"{c.post.id}" for c in cands)
            notes.append(f"• {p.id}: «{p.text.splitlines()[0][:30]}» — {len(cands)} ta mos e'lon ({names}), aniq emas")
    for x in unique:
        if not x.sold and SOLD_POSTS & set(x.message_ids):
            x.sold, x.reason = True, "egasi tasdiqladi"
    fresh_from = datetime.now(UTC) - timedelta(days=FRESH_DAYS)
    for x in unique:
        if not x.sold and x.post.date < fresh_from:
            x.stale = True
    recent = {x.post.id for x in unique if not x.sold and not x.stale}
    notes = [n for n in notes if "aniq emas" not in n or any(f"{i}" in n for i in recent)]
    return unique, notes


def title(x: Listing) -> str:
    p = x.parsed
    km = f", {p.mileage_km:,} km".replace(",", " ") if p.mileage_km else ""
    return f"{p.model or p.brand} {p.year or ''}{km}".strip()


async def ask_admins(username: str, doubtful: list, *, added: int, active: int, sold: int, stale: int) -> None:
    """Adminlarga: import xulosasi + har noaniq mashina uchun «Sotildimi?» kartasi (💰 Sotildi / ✅ Sotuvda)."""
    from aiogram import Bot
    from aiogram.enums import ParseMode
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot.services.car_cards import car_card_html

    if not settings.admin_telegram_ids:
        return
    bot = Bot(settings.bot_token)
    try:
        summary = (
            f"📥 <b>Kanal tarixi import qilindi</b> (@{username}, AI'siz)\n"
            f"✅ Sotuvda: {active} · 🔴 Sotilgan: {sold} · 🗄 Eski e'lon (arxiv): {stale}\n"
            f"Bazaga yangi yozildi: {added} ta."
        )
        if doubtful:
            summary += (
                f"\n\n❓ <b>{len(doubtful)} ta mashina aniq emas</b>: kanalda «… BARAKA BO'LDI» / «… SOTILDI» posti bor, "
                "lekin qaysi mashina ekani yozilmagan. Har biri uchun pastdagi tugmani bosing."
            )
        for aid in settings.admin_telegram_ids:
            await bot.send_message(aid, summary, parse_mode=ParseMode.HTML)
        for x, car in doubtful:
            doubts = "\n".join("• " + d.replace("{username}", username) for d in x.doubts[:3])
            header = f"❓ <b>Shu mashina sotildimi?</b>\nKanaldagi postlar:\n{html.escape(doubts)}"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="💰 Sotildi", callback_data=f"car:sold:{car.id}"),
                    InlineKeyboardButton(text="✅ Sotuvda", callback_data=f"car:keep:{car.id}"),
                ]]
            )
            for aid in settings.admin_telegram_ids:
                await bot.send_message(
                    aid, car_card_html(car, header=header), parse_mode=ParseMode.HTML, reply_markup=kb,
                    disable_web_page_preview=True,
                )
                await asyncio.sleep(0.4)
    finally:
        await bot.session.close()


async def main() -> None:
    username = (settings.channel_username or "").lstrip("@")
    chat_id = int(settings.channel_id)
    posts = fetch_posts(username)
    print(f"Kanal @{username}: {len(posts)} post ({posts[0].date:%Y-%m-%d} — {posts[-1].date:%Y-%m-%d})")
    unique, notes = analyse(posts)
    active = [x for x in unique if not x.sold and not x.stale]
    sold = [x for x in unique if x.sold]
    stale = [x for x in unique if x.stale]
    print(f"\n✅ SOTUVDA ({len(active)}):")
    for x in active:
        print(f"  {title(x)} — t.me/{username}/{x.post.id}")
    print(f"\n🔴 SOTILGAN ({len(sold)}):")
    for x in sold:
        print(f"  {title(x)} — {x.reason} — t.me/{username}/{x.post.id}")
    print(f"\n🗄 ESKI E'LON ({len(stale)}): {FRESH_DAYS} kundan beri qayta chiqmagan — arxivga (sotilgan deb hisoblanmaydi)")
    if notes:
        print("\n⚠️ Diqqat:")
        print("\n".join(notes))
    if not APPLY:
        print("\nSinov rejimi — bazaga yozilmadi (--apply bilan yoziladi).")
        return

    db_base.init_engine(settings.database_url, pool_size=2, max_overflow=0)
    added = skipped = 0
    async with db_base.get_session_factory()() as session:
        cars = CarRepository(session)
        created: dict[int, object] = {}
        for x in unique:
            if any([await cars.find_by_channel_message(chat_id, mid) for mid in x.message_ids]):
                skipped += 1
                continue
            car = await cars.create_from_parsed(
                x.parsed,
                source=CarSource.CHANNEL,
                raw_text=x.post.text,
                channel_chat_id=chat_id,
                channel_message_ids=x.message_ids,
                status=CarStatus.SOLD if x.sold else (CarStatus.ARCHIVED if x.stale else None),
                published_at=x.post.date,
            )
            car.post_texts = x.texts
            created[x.post.id] = car
            reason = x.reason or (f"{FRESH_DAYS} kundan eski e'lon" if x.stale else "")
            await cars.add_event(car, "imported", {"from": "public_web", "reason": reason})
            added += 1
        await session.commit()
        doubtful = [(x, created[x.post.id]) for x in active if x.doubts and x.post.id in created]
    await db_base.dispose_engine()
    print(f"\nBazaga yozildi: {added} ta yangi, {skipped} ta oldin bor edi.")
    await ask_admins(username, doubtful, added=added, active=len(active), sold=len(sold), stale=len(stale))


asyncio.run(main())
