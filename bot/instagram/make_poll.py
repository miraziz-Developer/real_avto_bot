"""Instagram kommentlarini davriy tekshirish (Make.com orqali) — Meta webhook'i kelmasa ham ishlaydi.

Make ssenariysi har N daqiqada:
  1. so'nggi postlarni oladi va bitta so'rov bilan yuboradi (stage=media): har post — id, kommentlar soni,
     havola, turi, video havolasi, matni;
  2. bot javobi `check` — kommentlar soni oshgan postlar; faqat shularning kommentlari olinadi (stage=comments);
  3. bot har yangi kommentga (bitta kommentga bir marta) javob tayyorlab `replies` qaytaradi, Make ularni yozadi.
Birinchi tekshiruv faqat holatni eslab qoladi — eski kommentlarga javob yozilmaydi. Make'da operatsiya tejaladi:
yangi komment bo'lmasa bir tekshiruv 3 operatsiya.

Make ma'lumotni «qatorlar» bilan yuboradi (JSON yig'ish alohida modul talab qiladi): qatorlar ␞ bilan,
maydonlar ␟ bilan ajratilgan; matn (eng oxirgi maydon) ichidagi ␞/␟ Make tomonida bo'shliqqa almashtiriladi.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.db.cars_repo import CarRepository
from bot.db.models import AppMeta
from bot.instagram.car_match import remember_post
from bot.instagram.manychat import read_payload

logger = logging.getLogger(__name__)

POLL_PATH = "/webhooks/make/poll"
ROW_SEP = "␞"  # ␞
FIELD_SEP = "␟"  # ␟
STATE_KEY = "ig_poll_state"
MAX_REPLIES_PER_POST = 10
MAX_TRIES = 2
# Shundan eski kommentlarga javob yozilmaydi (birinchi ishga tushirishdan oldingi kommentlar ham)
MAX_COMMENT_AGE = timedelta(days=2)


def parse_rows(raw: str, fields: int) -> list[list[str]]:
    rows = []
    for row in (raw or "").split(ROW_SEP):
        if not row.strip():
            continue
        parts = row.split(FIELD_SEP, fields - 1)
        rows.append([p.strip() for p in parts] + [""] * (fields - len(parts)))
    return rows


def _int(v: str) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _ts(v: str) -> datetime | None:
    try:
        return datetime.fromisoformat(v.replace("+0000", "+00:00").replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


class MakePoller:
    def __init__(self, bridge, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.bridge = bridge
        self.session_factory = session_factory

    async def _load(self, session: AsyncSession) -> dict:
        row = await session.get(AppMeta, STATE_KEY)
        if row is None:
            return {}
        try:
            return json.loads(row.value)
        except ValueError:
            return {}

    async def _save(self, session: AsyncSession, state: dict) -> None:
        value = json.dumps(state, ensure_ascii=False)
        row = await session.get(AppMeta, STATE_KEY)
        if row is None:
            session.add(AppMeta(key=STATE_KEY, value=value))
        else:
            row.value = value

    async def media(self, payload: dict) -> dict:
        """Postlar ro'yxati → kommentlari tekshiriladigan postlar."""
        rows = parse_rows(payload.get("rows", ""), 6)  # id, comments_count, permalink, media_type, media_url, caption
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            state = await self._load(session)
            first_run = "since" not in state
            counts: dict = state.get("counts", {})
            media: dict = state.get("media", {})
            pending = state.get("pending", {})
            if isinstance(pending, list):
                pending = dict.fromkeys(pending, 1)
            check: list[str] = []
            cars = CarRepository(session)
            for media_id, count_raw, permalink, media_type, media_url, caption in rows:
                if not media_id:
                    continue
                count = _int(count_raw)
                old = counts.get(media_id)
                if count is not None:
                    if not first_run and count > (old if old is not None else 0) and old != count:
                        check.append(media_id)
                    counts[media_id] = count
                media[media_id] = {"permalink": permalink, "type": media_type, "url": media_url, "caption": caption[:1500]}
                if caption or permalink:
                    await remember_post(cars, media_id, car=None, caption=caption or None, permalink=permalink or None)
            seen = {r[0] for r in rows}
            # Javobi kelmagan post yana bir marta tekshiriladi, keyin tashlab yuboriladi (operatsiya behuda ketmasin)
            retry = [m for m, tries in pending.items() if m in seen and m not in check and tries < MAX_TRIES]
            new_pending = dict.fromkeys(check, 1)
            new_pending.update({m: pending[m] + 1 for m in retry})
            check += retry
            state = {
                "since": state.get("since") or now.isoformat(),
                "counts": {k: v for k, v in counts.items() if k in seen},
                "media": {k: v for k, v in media.items() if k in seen},
                "pending": new_pending,
            }
            await self._save(session, state)
            await session.commit()
        logger.info(
            "IG poll media: %d post, kommentlar soni %d tasida, tekshiriladi: %s%s",
            len(rows), sum(1 for r in rows if _int(r[1]) is not None), check, " (birinchi tekshiruv)" if first_run else "",
        )
        return {"check": check}

    async def comments(self, payload: dict) -> dict:
        """Kommentlar (bir yoki bir nechta post) → yangi kommentlarga javoblar.

        Qator: id, timestamp, username, from_id, media_id, text. Make bir nechta post kommentlarini bitta so'rovga
        yig'ishi mumkin — har komment o'z posti (qatordagi media_id) bo'yicha ko'riladi.
        """
        rows = parse_rows(payload.get("rows", ""), 6)
        fallback_media = str(payload.get("media_id") or "").strip()
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            state = await self._load(session)
        since = _ts(state.get("since", "")) or now
        own = (settings.instagram_username or "").lstrip("@").lower()
        replies: list[dict] = []
        per_media: dict[str, int] = {}
        seen_media: set[str] = set()
        for comment_id, ts_raw, username, from_id, row_media, text in rows:
            media_id = row_media or fallback_media
            seen_media.add(media_id)
            ts = _ts(ts_raw)
            if not comment_id or not text or (own and username.lower() == own):
                continue
            if ts is not None and (ts < since or now - ts > MAX_COMMENT_AGE):
                continue
            if per_media.get(media_id, 0) >= MAX_REPLIES_PER_POST:
                continue
            info = state.get("media", {}).get(media_id, {})
            result = await self.bridge.answer(
                {
                    "event": "comment",
                    "dm": "false",
                    "user_id": from_id,
                    "ig_username": username,
                    "text": text,
                    "comment_id": comment_id,
                    "media_id": media_id,
                    "caption": info.get("caption", ""),
                    "post_url": info.get("permalink", ""),
                    "media_url": info.get("url", "") if info.get("type") in ("VIDEO", "REELS") else "",
                    "media_type": info.get("type", ""),
                }
            )
            if result.get("public_reply"):
                replies.append({"comment_id": comment_id, "message": result["public_reply"]})
                per_media[media_id] = per_media.get(media_id, 0) + 1
        if fallback_media:
            seen_media.add(fallback_media)
        async with self.session_factory() as session:
            state = await self._load(session)
            pending = state.get("pending", {})
            if isinstance(pending, list):
                pending = dict.fromkeys(pending, 1)
            state["pending"] = {m: t for m, t in pending.items() if m not in seen_media}
            for media_id, n in per_media.items():
                if media_id in state.get("counts", {}):
                    state["counts"][media_id] += n  # o'zimizning javoblarimiz ham kommentlar sonini oshiradi
            await self._save(session, state)
            await session.commit()
        logger.info("IG poll comments: %d post, %d komment, %d javob", len(seen_media), len(rows), len(replies))
        return {"replies": replies, "count": len(replies)}

    async def handle(self, request: web.Request) -> web.Response:
        if not self.bridge._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = {k: str(v) for k, v in (await read_payload(request)).items()}
        except ValueError:
            return web.json_response({"error": "form kerak"}, status=400)
        stage = payload.get("stage", "")
        if stage not in ("media", "comments"):
            logger.info(
                "IG poll: noma'lum so'rov (content-type=%s, encoding=%s, maydonlar=%s)",
                request.content_type, request.headers.get("Content-Encoding", ""), sorted(payload)[:10],
            )
        try:
            if stage == "media":
                return web.json_response(await self.media(payload))
            if stage == "comments":
                return web.json_response(await self.comments(payload))
        except Exception:
            logger.exception("IG poll xatosi (%s)", stage)
            return web.json_response({"check": [], "replies": [], "count": 0})
        return web.json_response({"error": "stage: media | comments"}, status=400)

    def add_routes(self, app: web.Application) -> None:
        app.router.add_post(POLL_PATH, self.handle)
