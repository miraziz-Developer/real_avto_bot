"""Mashinalar bazasi (cars + car_events) bilan ishlash."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Car, CarEvent, CarSource, CarStatus, ChannelThread
from bot.services.car_parser import REQUIRED_FIELDS, ParsedCar, detect_brand_model, normalize_text

# ParsedCar → Car ga ko'chiriladigan maydonlar
PARSED_FIELDS = (
    "brand",
    "model",
    "year",
    "mileage_km",
    "price_usd",
    "color",
    "transmission",
    "fuel",
    "position",
    "paint_status",
    "has_accident",
    "location",
    "notes",
)

# Savdo agenti mijozga taklif qila oladigan holatlar (sotilgan/arxiv hech qachon)
OFFERABLE_STATUSES = (CarStatus.ACTIVE, CarStatus.RESERVED)

# Admin qo'lda tahrirlay oladigan maydonlar
EDITABLE_FIELDS = frozenset(PARSED_FIELDS) | {"purchase_price_usd", "expenses_usd", "sold_price_usd", "is_own"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CarRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _event(self, car: Car, kind: str, data: dict | None = None, actor: int | None = None) -> None:
        self.session.add(CarEvent(car_id=car.id, kind=kind, data=data, actor_telegram_id=actor))

    async def has_event(self, car: Car, kind: str) -> bool:
        stmt = select(CarEvent.id).where(CarEvent.car_id == car.id, CarEvent.kind == kind).limit(1)
        return (await self.session.execute(stmt)).first() is not None

    async def add_event(self, car: Car, kind: str, data: dict | None = None) -> None:
        await self._event(car, kind, data)

    async def remember_thread(
        self, *, group_chat_id: int, thread_message_id: int, channel_chat_id: int, channel_message_id: int
    ) -> None:
        """Muhokama guruhidagi avto-forward xabar qaysi kanal postiga tegishli — kommentlar uchun."""
        exists = await self.session.execute(
            select(ChannelThread.id).where(
                ChannelThread.group_chat_id == group_chat_id, ChannelThread.thread_message_id == thread_message_id
            )
        )
        if exists.first() is None:
            self.session.add(
                ChannelThread(
                    group_chat_id=group_chat_id,
                    thread_message_id=thread_message_id,
                    channel_chat_id=channel_chat_id,
                    channel_message_id=channel_message_id,
                )
            )
            await self.session.flush()

    async def channel_post_for_thread(self, group_chat_id: int, thread_message_id: int) -> tuple[int, int] | None:
        row = (
            await self.session.execute(
                select(ChannelThread.channel_chat_id, ChannelThread.channel_message_id).where(
                    ChannelThread.group_chat_id == group_chat_id, ChannelThread.thread_message_id == thread_message_id
                )
            )
        ).first()
        return (int(row[0]), int(row[1])) if row else None

    async def get(self, car_id: int) -> Car | None:
        return await self.session.get(Car, car_id)

    async def find_by_channel_message(self, chat_id: int, message_id: int) -> Car | None:
        stmt = (
            select(Car)
            .where(Car.channel_chat_id == chat_id, Car.channel_message_ids.any(message_id))
            .order_by(Car.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_listing(self, listing_id: int) -> Car | None:
        stmt = select(Car).where(Car.listing_submission_id == listing_id).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create_from_parsed(
        self,
        parsed: ParsedCar,
        *,
        source: str,
        raw_text: str,
        photo_file_ids: list[str] | None = None,
        video_file_ids: list[str] | None = None,
        channel_chat_id: int | None = None,
        channel_message_ids: list[int] | None = None,
        listing_submission_id: int | None = None,
        status: str | None = None,
        published_at: datetime | None = None,
    ) -> Car:
        if status is None:
            status = CarStatus.ACTIVE if parsed.is_complete() else CarStatus.REVIEW
        car = Car(
            status=status,
            source=source,
            raw_text=raw_text or "",
            photo_file_ids=list(photo_file_ids or []),
            video_file_ids=list(video_file_ids or []),
            channel_chat_id=channel_chat_id,
            channel_message_ids=list(channel_message_ids or []),
            listing_submission_id=listing_submission_id,
            ai_confidence=parsed.confidence,
            ai_data=parsed.extra or None,
            published_at=published_at or _now(),
            **{f: getattr(parsed, f) for f in PARSED_FIELDS},
        )
        if status == CarStatus.SOLD:
            car.sold_at = car.published_at
        self.session.add(car)
        await self.session.flush()
        await self._event(car, "created", {"source": source, "status": status, "price_usd": car.price_usd})
        return car

    async def apply_parsed(
        self, car: Car, parsed: ParsedCar, *, raw_text: str | None = None, allow_activate: bool = True
    ) -> dict[str, Any]:
        """Tahrirlangan post: topilgan qiymatlarni yangilash. O'zgarishlar ro'yxatini qaytaradi."""
        changes: dict[str, Any] = {}
        for f in PARSED_FIELDS:
            new = getattr(parsed, f)
            if new in (None, ""):
                continue
            old = getattr(car, f)
            if old != new:
                changes[f] = {"old": old, "new": new}
                setattr(car, f, new)
        if raw_text is not None:
            car.raw_text = raw_text
        if parsed.confidence is not None:
            car.ai_confidence = parsed.confidence
        if "price_usd" in changes:
            await self._event(car, "price_changed", changes["price_usd"])
        if changes:
            await self._event(car, "edited", {k: v for k, v in changes.items() if k != "price_usd"} or None)
        # Admin tekshiruvidagi mashina tahrirdan keyin to'liq bo'lib qolsa — sotuvga chiqadi
        # allow_activate=False — ma'lumot faqat ovozdan kelgan: admin tasdig'isiz sotuvga chiqarmaymiz
        if allow_activate and car.status == CarStatus.REVIEW and all(getattr(car, f) for f in REQUIRED_FIELDS):
            await self.set_status(car, CarStatus.ACTIVE)
        return changes

    async def update_fields(self, car: Car, values: dict[str, Any], *, actor: int | None = None) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        for k, v in values.items():
            if k not in EDITABLE_FIELDS:
                continue
            old = getattr(car, k)
            if old != v:
                changes[k] = {"old": old, "new": v}
                setattr(car, k, v)
        if "price_usd" in changes:
            await self._event(car, "price_changed", changes["price_usd"], actor)
        if changes:
            await self._event(car, "edited", changes, actor)
        return changes

    async def set_status(
        self,
        car: Car,
        status: str,
        *,
        actor: int | None = None,
        sold_price_usd: int | None = None,
    ) -> bool:
        if car.status == status:
            return False
        old = car.status
        car.status = status
        now = _now()
        if status == CarStatus.SOLD:
            car.sold_at = now
            if sold_price_usd is not None:
                car.sold_price_usd = sold_price_usd
        elif old == CarStatus.SOLD:
            car.sold_at = None
        if status == CarStatus.ACTIVE:
            car.stale_prompted_at = None
            if car.published_at is None:
                car.published_at = now
        await self._event(car, "status_changed", {"old": old, "new": status}, actor)
        return True

    async def mark_stale_prompted(self, car: Car) -> None:
        car.stale_prompted_at = _now()

    async def stale_active_cars(self, *, older_than_days: int, reprompt_days: int = 7, limit: int = 20) -> list[Car]:
        now = _now()
        stmt = (
            select(Car)
            .where(
                Car.status == CarStatus.ACTIVE,
                Car.published_at < now - timedelta(days=max(1, older_than_days)),
                or_(Car.stale_prompted_at.is_(None), Car.stale_prompted_at < now - timedelta(days=reprompt_days)),
            )
            .order_by(Car.published_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_by_status(self, status: str, *, limit: int = 30) -> list[Car]:
        stmt = select(Car).where(Car.status == status).order_by(Car.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def search_offerable(
        self,
        *,
        query: str | None = None,
        brand: str | None = None,
        model: str | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        price_min_usd: int | None = None,
        price_max_usd: int | None = None,
        transmission: str | None = None,
        fuel: str | None = None,
        exclude_ids: list[int] | None = None,
        limit: int = 5,
    ) -> list[Car]:
        """Mijozga taklif qilish mumkin bo'lgan (sotuvda/bron) mashinalar. Slang nomlar katalog orqali tushuniladi."""
        conds = [Car.status.in_(OFFERABLE_STATUSES)]
        # «jentra», «кобальт» → Gentra, Cobalt
        raw_name = " ".join(p for p in (brand, model, query) if p)
        if raw_name:
            det_brand, det_model = detect_brand_model(normalize_text(raw_name))
            name_model = det_model or model
            name_brand = det_brand or brand
            if name_model:
                conds.append(Car.model.ilike(f"%{name_model.split()[0]}%"))
            elif name_brand:
                conds.append(Car.brand.ilike(f"%{name_brand}%"))
            elif query:
                conds.append(Car.raw_text.ilike(f"%{query.strip()[:60]}%"))
        if year_min:
            conds.append(Car.year >= year_min)
        if year_max:
            conds.append(Car.year <= year_max)
        if price_min_usd:
            conds.append(Car.price_usd >= price_min_usd)
        if price_max_usd:
            # Byudjetdan biroz (10%) qimmatini ham ko'rsatamiz — savdolashish uchun joy bor
            conds.append(Car.price_usd <= int(price_max_usd * 1.1))
        if transmission:
            conds.append(Car.transmission == transmission)
        if fuel:
            conds.append(Car.fuel.ilike(f"%{fuel}%"))
        if exclude_ids:
            conds.append(Car.id.not_in(exclude_ids))
        order = [Car.status.asc()]  # active < reserved — avval sotuvdagilar
        if price_max_usd:
            order.append(func.abs(Car.price_usd - price_max_usd).asc())
        order.append(Car.published_at.desc().nulls_last())
        stmt = select(Car).where(*conds).order_by(*order).limit(max(1, min(limit, 10)))
        return list((await self.session.execute(stmt)).scalars().all())

    async def similar_offerable(self, car: Car, *, limit: int = 3) -> list[Car]:
        """Sotilgan/yo'q mashina o'rniga o'xshashlari: avval shu model, keyin shu narx oralig'i."""
        same = await self.search_offerable(model=car.model, brand=car.brand, exclude_ids=[car.id], limit=limit)
        if len(same) >= limit or not car.price_usd:
            return same
        band = await self.search_offerable(
            price_min_usd=int(car.price_usd * 0.8),
            price_max_usd=int(car.price_usd * 1.1),
            exclude_ids=[car.id, *[c.id for c in same]],
            limit=limit - len(same),
        )
        return same + band

    async def stats(self, *, days: int = 30) -> dict[str, Any]:
        """Jamoa uchun qisqa statistika (bot ichidagi /statistika va CRM bilan bir xil mantiq)."""
        since = _now() - timedelta(days=days)
        by_status = dict(
            (await self.session.execute(select(Car.status, func.count()).group_by(Car.status))).all()
        )
        active_value = (
            await self.session.execute(
                select(func.coalesce(func.sum(Car.price_usd), 0)).where(Car.status == CarStatus.ACTIVE)
            )
        ).scalar_one()
        sold_q = select(Car).where(Car.status == CarStatus.SOLD, Car.sold_at >= since)
        sold = list((await self.session.execute(sold_q)).scalars().all())
        days_to_sell = [
            (c.sold_at - c.published_at).total_seconds() / 86400
            for c in sold
            if c.sold_at and c.published_at and c.sold_at >= c.published_at
        ]
        top: dict[str, int] = {}
        for c in sold:
            key = " ".join(p for p in (c.brand, c.model) if p) or "Noma'lum"
            top[key] = top.get(key, 0) + 1
        profit = sum(
            (c.sold_price_usd or c.price_usd or 0) - (c.purchase_price_usd or 0) - (c.expenses_usd or 0)
            for c in sold
            if c.is_own and c.purchase_price_usd
        )
        new_count = (
            await self.session.execute(
                select(func.count()).select_from(Car).where(and_(Car.created_at >= since, Car.source != CarSource.IMPORT))
            )
        ).scalar_one()
        return {
            "days": days,
            "by_status": {str(k): int(v) for k, v in by_status.items()},
            "active_value_usd": int(active_value or 0),
            "new_count": int(new_count or 0),
            "sold_count": len(sold),
            "avg_days_to_sell": round(sum(days_to_sell) / len(days_to_sell), 1) if days_to_sell else None,
            "top_sold_models": sorted(top.items(), key=lambda kv: -kv[1])[:5],
            "own_profit_usd": int(profit),
        }
