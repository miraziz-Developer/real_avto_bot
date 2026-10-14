"""Instagram komment/xabar qaysi mashina haqida — aniqlash.

Tartib (birinchi topilgani):
  1. aniq ko'rsatma: ManyChat avtomatizatsiyasida berilgan mashina raqami («17», «#17»);
  2. shu post oldin bog'langan (instagram_posts: admin tanlovi yoki avvalgi avtomatik natija);
  3. post matni (caption) — kod («ID: 17») yoki model/yil/probeg/narx bo'yicha bazadagi mashina;
  4. mijoz yozgan mashina raqami («17», «kod 17») — reel matnida «Kod: 17» deb yozib qo'yilsa qulay;
  5. komment matnidagi model — sotuvdagilar orasida bittagina mos bo'lsa.
Topilmasa — None; post ma'lum bo'lsa adminlardan «bu post qaysi mashina?» deb so'raladi (chaqiruvchi).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select

from bot.config import settings
from bot.db.cars_repo import CarRepository, same_car_model
from bot.db.models import Car, CarStatus, InstagramPost
from bot.services.car_parser import ParsedCar, parse_car_text

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reels?|tv)/([\w-]{5,})", re.IGNORECASE)
_EXPLICIT_RE = re.compile(r"^\s*#?\s*(\d{1,7})\s*$")
# Postga qo'yilgan kod: «ID: 17», «kod 17», «#ra17»
_CAPTION_CODE_RE = re.compile(r"(?:\bID|\bkod|\bкод|#ra)\s*[:№#-]?\s*(\d{1,7})\b", re.IGNORECASE)
# Mijoz kommentda/Direct'da mashina raqamini yozdi: «17», «#17», «kod 17», «ID: 17»
_TEXT_CODE_ONLY_RE = re.compile(r"^\s*(?:#|№|kod|код|id)?\s*[:-]?\s*(\d{1,5})\s*[.!?)]*\s*$", re.IGNORECASE)
_TEXT_CODE_RE = re.compile(r"(?:#|№|\bkod|\bкод|\bID)\s*[:-]?\s*(\d{1,5})\b", re.IGNORECASE)
# Narx «bir xil» deyiladigan farq (post va bazada yaxlitlash farqi)
_PRICE_TOLERANCE = 0.03
MIN_CAPTION_SCORE = 3


@dataclass
class CarMatch:
    car: Car | None
    how: str  # explicit | mapped | caption | text | none
    post: InstagramPost | None = None


def post_ref(media_id: str | None, post_url: str | None) -> str | None:
    """Post kaliti: media ID (raqam) yoki havoladagi qisqa kod."""
    media_id = (media_id or "").strip()
    if media_id and media_id.isdigit():
        return media_id
    m = _SHORTCODE_RE.search(post_url or "")
    return m.group(1) if m else None


def explicit_car_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    m = _EXPLICIT_RE.match(str(value))
    return int(m.group(1)) if m else None


def _score(car: Car, p: ParsedCar) -> int:
    """Post matni va bazadagi mashina qanchalik mos: qarama-qarshi fakt bo'lsa -100."""
    score = 0
    if p.model and car.model:
        if not same_car_model(car.model, p.model):
            return -100
        score += 1
    for field, weight in (("year", 2), ("mileage_km", 3)):
        a, b = getattr(car, field), getattr(p, field)
        if a and b:
            if a != b:
                return -100
            score += weight
    if car.price_usd and p.price_usd:
        if abs(car.price_usd - p.price_usd) <= car.price_usd * _PRICE_TOLERANCE:
            score += 2
        else:
            score -= 1  # narx o'zgargan bo'lishi mumkin — rad etmaymiz
    if car.color and p.color:
        score += 1 if car.color.split()[0].lower() == p.color.split()[0].lower() else -1
    return score


async def match_caption(cars: CarRepository, caption: str | None) -> Car | None:
    caption = (caption or "").strip()
    if not caption:
        return None
    code = _CAPTION_CODE_RE.search(caption)
    if code:
        car = await cars.get(int(code.group(1)))
        if car is not None:
            return car
    return await match_parsed(cars, parse_car_text(caption, usd_rate_uzs=settings.usd_rate_uzs))


async def match_parsed(cars: CarRepository, parsed: ParsedCar) -> Car | None:
    """Post/video'dan olingan ma'lumot (model, yil, probeg, narx, rang) bo'yicha bazadagi yagona mos mashina."""
    candidates = await cars.find_post_candidates(parsed)
    if not candidates:
        return None
    scored = sorted(((_score(c, parsed), c) for c in candidates), key=lambda x: (x[0], x[1].id), reverse=True)
    best_score, best = scored[0]
    if best_score < 0:
        return None
    runner_up = scored[1][0] if len(scored) > 1 else None
    if runner_up is not None and runner_up == best_score:
        return None  # ikki mashina teng mos — taxmin qilmaymiz, admin tanlaydi
    if best_score >= MIN_CAPTION_SCORE or (len(candidates) == 1 and best_score >= 1):
        return best
    return None


def text_car_code(text: str | None) -> int | None:
    m = _TEXT_CODE_ONLY_RE.match(text or "") or _TEXT_CODE_RE.search(text or "")
    return int(m.group(1)) if m else None


async def match_code(cars: CarRepository, text: str | None) -> Car | None:
    code = text_car_code(text)
    if code is None:
        return None
    car = await cars.get(code)
    return car if car is not None and car.status != CarStatus.ARCHIVED else None


async def match_text(cars: CarRepository, text: str | None) -> Car | None:
    """Komment matnida model aytilgan («Cobalt narxi?») va sotuvda shunday bittagina mashina bo'lsa."""
    parsed = parse_car_text(text or "", usd_rate_uzs=settings.usd_rate_uzs)
    if not parsed.model:
        return None
    rows = await cars.search_offerable(brand=parsed.brand, model=parsed.model, limit=3)
    if parsed.year:
        rows = [c for c in rows if not c.year or c.year == parsed.year]
    return rows[0] if len(rows) == 1 else None


async def get_post(cars: CarRepository, ref: str) -> InstagramPost | None:
    return (
        await cars.session.execute(select(InstagramPost).where(InstagramPost.ref == ref))
    ).scalar_one_or_none()


async def remember_post(
    cars: CarRepository,
    ref: str,
    *,
    car: Car | None,
    caption: str | None = None,
    permalink: str | None = None,
    source: str = "auto",
) -> InstagramPost:
    post = await get_post(cars, ref)
    if post is None:
        post = InstagramPost(ref=ref)
        cars.session.add(post)
    if caption and not post.caption:
        post.caption = caption[:4000]
    if permalink and not post.permalink:
        post.permalink = permalink[:300]
    # Admin tanlovini avtomatik natija almashtirmaydi
    if car is not None and (source == "admin" or post.source != "admin" or post.car_id is None):
        post.car_id = car.id
        post.source = source
    await cars.session.flush()
    return post


async def resolve_car(
    cars: CarRepository,
    *,
    explicit: str | int | None = None,
    ref: str | None = None,
    caption: str | None = None,
    permalink: str | None = None,
    text: str | None = None,
) -> CarMatch:
    car_id = explicit_car_id(explicit)
    if car_id is not None:
        car = await cars.get(car_id)
        if car is not None:
            post = await remember_post(cars, ref, car=car, caption=caption, permalink=permalink, source="admin") if ref else None
            return CarMatch(car, "explicit", post)
    post = await get_post(cars, ref) if ref else None
    if post is not None and post.car_id:
        car = await cars.get(post.car_id)
        if car is not None:
            return CarMatch(car, "mapped", post)
    car = await match_caption(cars, caption or (post.caption if post else None))
    if car is not None:
        if ref:
            post = await remember_post(cars, ref, car=car, caption=caption, permalink=permalink)
        return CarMatch(car, "caption", post)
    if ref:
        post = await remember_post(cars, ref, car=None, caption=caption, permalink=permalink)
    car = await match_code(cars, text)
    if car is not None:
        return CarMatch(car, "code", post)
    car = await match_text(cars, text)
    return CarMatch(car, "text" if car else "none", post)
