"""Kanal tarixini mashinalar bazasiga import qilish (Telegram Desktop eksporti).

Bot kanalga qo'shilishidan oldingi postlarni Telegram API orqali o'qib bo'lmaydi, shuning uchun:
  1. Telegram Desktop → kanal → ⋮ → Export chat history → format: JSON (rasmlar shart emas)
  2. python -m scripts.import_channel_export path/to/result.json --dry-run   (avval natijani ko'rish)
  3. python -m scripts.import_channel_export path/to/result.json             (bazaga yozish)

Qoidalar:
  • albom rasmlari (matnsiz, bir necha soniya ichida kelgan) bitta e'longa yig'iladi;
  • postda yoki unga reply'da «sotildi» bo'lsa — status SOLD;
  • --active-days dan eski, sotilganligi noma'lum postlar ARCHIVED (agent eski mashinani taklif qilmasligi uchun);
    kerak bo'lsa admin /mashina ID orqali qayta sotuvga chiqaradi;
  • qayta ishga tushirish xavfsiz — bazada bor postlar o'tkazib yuboriladi.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, UTC
from pathlib import Path

from bot.ai import get_ai
from bot.config import settings
from bot.db.base import create_tables, dispose_engine, get_engine, get_session_factory, init_engine
from bot.db.cars_repo import CarRepository
from bot.db.migrate import apply_car_indexes
from bot.db.models import CarSource, CarStatus
from bot.services.car_extract import extract_car
from bot.services.car_parser import is_sold_text

logger = logging.getLogger("import_channel_export")

ALBUM_GAP_SECONDS = 5


@dataclass
class Post:
    ids: list[int]
    date: datetime
    text: str
    has_media: bool
    sold_by_reply: bool = False


def _flatten_text(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in raw)
    return ""


def _msg_date(m: dict) -> datetime:
    if m.get("date_unixtime"):
        return datetime.fromtimestamp(int(m["date_unixtime"]), tz=UTC)
    # Eski eksportlarda faqat mahalliy vaqt — Toshkent (UTC+5) deb olamiz
    return datetime.fromisoformat(m["date"]).replace(tzinfo=timezone(timedelta(hours=5)))


def group_posts(messages: list[dict]) -> list[Post]:
    """Eksport xabarlarini e'lonlarga yig'ish: matnli xabar + undan keyin darhol kelgan matnsiz rasmlar."""
    msgs = sorted((m for m in messages if m.get("type") == "message"), key=lambda m: int(m["id"]))
    posts: list[Post] = []
    for m in msgs:
        text = _flatten_text(m.get("text")).strip()
        date = _msg_date(m)
        media = bool(m.get("photo") or m.get("media_type") or m.get("file"))
        reply_to = m.get("reply_to_message_id")

        # «SOTILDI» reply — asl postni belgilaymiz, alohida e'lon emas
        if reply_to and is_sold_text(text):
            target = next((p for p in reversed(posts) if int(reply_to) in p.ids), None)
            if target is not None:
                target.sold_by_reply = True
            continue

        prev = posts[-1] if posts else None
        same_album = (
            prev is not None
            and media
            and prev.has_media
            and (not text or not prev.text)
            and (date - prev.date).total_seconds() <= ALBUM_GAP_SECONDS
        )
        if same_album:
            prev.ids.append(int(m["id"]))
            if text and not prev.text:
                prev.text = text
            continue
        posts.append(Post(ids=[int(m["id"])], date=date, text=text, has_media=media))
    return posts


def _channel_chat_id(export: dict) -> int | None:
    raw = export.get("id")
    if raw is None:
        return None
    s = str(raw)
    return int(s) if s.startswith("-100") else int(f"-100{s}")


async def run(path: Path, *, dry_run: bool, use_ai: bool, active_days: int, limit: int | None) -> None:
    export = json.loads(path.read_text(encoding="utf-8"))
    chat_id = _channel_chat_id(export)
    posts = [p for p in group_posts(export.get("messages", [])) if p.text]
    if limit:
        posts = posts[-limit:]
    print(f"Kanal: {export.get('name')} (chat_id={chat_id}) — matnli postlar: {len(posts)}")

    ai = get_ai() if use_ai else None
    if use_ai and not (ai and ai.enabled):
        print("⚠️  GROQ_API_KEY yo'q — faqat oddiy (regex) tahlil ishlatiladi")
        ai = None

    init_engine(settings.database_url, pool_size=2, max_overflow=0)
    await create_tables()
    await apply_car_indexes(get_engine())
    cutoff = datetime.now(UTC) - timedelta(days=active_days)
    counts: dict[str, int] = {"bazada_bor": 0, "elon_emas": 0}
    try:
        async with get_session_factory()() as session:
            cars = CarRepository(session)
            for i, post in enumerate(posts, 1):
                if chat_id is not None and await cars.find_by_channel_message(chat_id, post.ids[0]) is not None:
                    counts["bazada_bor"] += 1
                    continue
                parsed = await extract_car(post.text, ai=ai, usd_rate_uzs=settings.usd_rate_uzs)
                if not parsed.looks_like_car():
                    counts["elon_emas"] += 1
                    continue
                if post.sold_by_reply or is_sold_text(post.text):
                    status = CarStatus.SOLD
                elif post.date < cutoff:
                    status = CarStatus.ARCHIVED
                else:
                    status = CarStatus.ACTIVE if parsed.is_complete() else CarStatus.REVIEW
                counts[status] = counts.get(status, 0) + 1
                title = " ".join(str(x) for x in (parsed.brand, parsed.model, parsed.year) if x) or "?"
                price = f"${parsed.price_usd:,}" if parsed.price_usd else "narx?"
                print(f"  [{i}/{len(posts)}] #{post.ids[0]} {post.date:%Y-%m-%d} {title} {price} → {status}")
                if dry_run:
                    continue
                await cars.create_from_parsed(
                    parsed,
                    source=CarSource.IMPORT,
                    raw_text=post.text,
                    channel_chat_id=chat_id,
                    channel_message_ids=post.ids,
                    status=status,
                    published_at=post.date,
                )
                if i % 50 == 0:
                    await session.commit()
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
    finally:
        if ai is not None:
            await ai.close()
        await dispose_engine()

    print("\nNatija" + (" (dry-run, bazaga yozilmadi)" if dry_run else "") + ":")
    for k, v in counts.items():
        print(f"  {k}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Telegram Desktop eksportidan kanal postlarini mashinalar bazasiga import")
    ap.add_argument("export_json", type=Path, help="result.json yo'li")
    ap.add_argument("--dry-run", action="store_true", help="faqat ko'rsatish, bazaga yozmaslik")
    ap.add_argument("--ai", action="store_true", help="Groq AI bilan tahlil (sekinroq, aniqroq)")
    ap.add_argument("--active-days", type=int, default=30, help="shundan eski postlar arxivga (default 30 kun)")
    ap.add_argument("--limit", type=int, default=None, help="faqat oxirgi N ta post")
    args = ap.parse_args()
    if not args.export_json.is_file():
        sys.exit(f"Fayl topilmadi: {args.export_json}")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(
        run(args.export_json, dry_run=args.dry_run, use_ai=args.ai, active_days=args.active_days, limit=args.limit)
    )


if __name__ == "__main__":
    main()
