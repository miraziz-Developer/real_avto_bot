"""Ochiq kanalning veb ko'rinishi (t.me/s/<kanal>) — AI'siz o'qish.

Veb ko'rinish postning HOZIRGI (tahrirlangan) matnini ko'rsatadi: «SOTILDI» qo'shilgani yoki telefon olib
tashlangani shu yerdan ko'rinadi, bot o'chiq bo'lgan paytda chiqqan postlar ham. Kanal sinxronizatsiyasi
(bot/workers/channel_sync.py) va tarixni import qilish skripti shu moduldan foydalanadi.

Kanal formati (@REAL_AVTO_ARZON): 1–2 dumaloq video + «🚘AVTOMOBIL: … / yili / probeg / 📞Aloqa» posti.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp

logger = logging.getLogger(__name__)

# E'londan oldingi dumaloq videolar shu vaqt ichida chiqqan bo'lsa — o'sha e'longa tegishli
ROUND_VIDEO_GAP = timedelta(minutes=5)
_LISTING_RE = re.compile(r"(?:avtomobil|yili)\s*:", re.IGNORECASE)
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)


@dataclass
class WebPost:
    id: int
    date: datetime
    text: str
    round_video: bool = False
    forwarded: bool = False


@dataclass
class WebListing:
    post: WebPost
    message_ids: list[int] = field(default_factory=list)  # e'lon + oldidagi dumaloq videolar


def parse_page(page: str, username: str) -> list[WebPost]:
    posts: list[WebPost] = []
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        m = re.search(rf'data-post="{re.escape(username)}/(\d+)"', block, re.IGNORECASE)
        dt = re.search(r'<time datetime="([^"]+)"', block)
        if not m or not dt:
            continue
        t = _TEXT_RE.search(block)
        text = ""
        if t:
            text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", t.group(1)))).strip()
        try:
            date = datetime.fromisoformat(dt.group(1))
        except ValueError:
            continue
        posts.append(
            WebPost(
                id=int(m.group(1)),
                date=date,
                text=text,
                round_video="tgme_widget_message_roundvideo" in block,
                forwarded="tgme_widget_message_forwarded_from" in block,
            )
        )
    return posts


async def _get(session: aiohttp.ClientSession, url: str, *, attempts: int = 4) -> str:
    for attempt in range(attempts):
        try:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status == 200:
                    page = await r.text(errors="replace")
                    if "tgme_widget_message_wrap" in page:
                        return page
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.info("t.me sahifasi olinmadi (%s): %s", url, e)
        # Telegram tez so'rovlarga vaqtincha bo'sh sahifa qaytaradi — kutib qayta urinamiz
        await asyncio.sleep(3 * (attempt + 1))
    return ""


async def fetch_posts(username: str, *, since: datetime, max_pages: int = 60) -> tuple[list[WebPost], bool]:
    """`since` dan keyingi postlar (eskidan yangiga) va `since` gacha to'liq o'qildimi (bo'sh sahifa uzib qo'ymadi)."""
    username = username.lstrip("@")
    posts: dict[int, WebPost] = {}
    before: int | None = None
    complete = False
    timeout = aiohttp.ClientTimeout(total=40)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(max_pages):
            url = f"https://t.me/s/{username}" + (f"?before={before}" if before else "")
            page_posts = parse_page(await _get(session, url), username)
            if not page_posts:
                break
            for p in page_posts:
                posts[p.id] = p
            oldest = min(page_posts, key=lambda p: p.id)
            if oldest.date < since or oldest.id <= 1:
                complete = True
                break
            before = oldest.id
            await asyncio.sleep(1)
    return sorted((p for p in posts.values() if p.date >= since), key=lambda p: p.id), complete


def is_listing(post: WebPost) -> bool:
    return bool(post.text) and bool(_LISTING_RE.search(post.text))


def listings(posts: list[WebPost]) -> list[WebListing]:
    """E'lon postlari + har biriga oldidan chiqqan dumaloq videolar (posts — ID bo'yicha tartiblangan)."""
    out: list[WebListing] = []
    for i, p in enumerate(posts):
        if not is_listing(p):
            continue
        ids = [p.id]
        j = i - 1
        while j >= 0 and posts[j].round_video and not posts[j].forwarded and p.date - posts[j].date <= ROUND_VIDEO_GAP:
            ids.append(posts[j].id)
            j -= 1
        out.append(WebListing(p, ids))
    return out
