"""AI maslahatchini Real Avto bazasi (e'lonlar, leadlar, suhbatlar) va Telegramga ulash.

Telegram handler ham, Instagram (Make.com / ManyChat) webhook ham shu servisdan foydalanadi —
bitta «miya», bir nechta kanal.
"""

from __future__ import annotations

import html
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import (
    AiConversation,
    Lead,
    LeadStatus,
    ListingSubmission,
    ListingSubmissionStatus,
)
from bot.db.repositories import CrmRepository
from bot.services.ai import (
    AssistantReply,
    CarSearch,
    ClientContext,
    LeadInput,
    OpenAICompatibleClient,
    SalesAssistant,
)
from bot.utils.listing_links import channel_post_url

logger = logging.getLogger(__name__)

STORED_HISTORY_MESSAGES = 40
LEAD_DEDUP_WINDOW = timedelta(hours=12)

_CONDITION_LABELS = {
    "ideal": "ideal",
    "yaxshi": "yaxshi",
    "qoniqarli": "qoniqarli",
    "tamir": "ta'mir talab",
}
_BRAND_SYNONYMS = ({"chevrolet", "daewoo", "ravon"},)


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _word_condition(word: str):
    w = word.lower()
    pattern = f"%{_like_escape(w)}%"
    cond = or_(ListingSubmission.brand.ilike(pattern, escape="\\"), ListingSubmission.model.ilike(pattern, escape="\\"))
    for group in _BRAND_SYNONYMS:
        if w in group:
            cond = or_(cond, *(ListingSubmission.brand.ilike(b) for b in group))
    return cond


def car_to_dict(sub: ListingSubmission) -> dict[str, Any]:
    url = None
    if sub.channel_message_id:
        url = channel_post_url(
            channel_id=settings.channel_id,
            message_id=int(sub.channel_message_id),
            public_username=settings.channel_username,
        )
    extra = (sub.extra_details or "").strip()
    return {
        "listing_id": sub.id,
        "car": f"{sub.brand} {sub.model}",
        "year": sub.year,
        "mileage_km": sub.mileage,
        "price_usd": int(sub.price_ask_usd),
        "condition": _CONDITION_LABELS.get(sub.condition_key, sub.condition_key),
        "accident": bool(sub.has_accident),
        "paint": sub.paint_status,
        "location": sub.location,
        "details": extra[:300] if extra else None,
        "url": url,
    }


class DbCarCatalog:
    """Kanalda turgan (tasdiqlangan, sotilmagan) e'lonlar — AI uchun katalog."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _base(self):
        return select(ListingSubmission).where(
            ListingSubmission.status == ListingSubmissionStatus.APPROVED,
            ListingSubmission.channel_message_id.is_not(None),
            or_(ListingSubmission.sale_status.is_(None), ListingSubmission.sale_status != "sold"),
        )

    async def _run(self, params: CarSearch, words: list[str]) -> list[ListingSubmission]:
        stmt = self._base()
        for w in words:
            stmt = stmt.where(_word_condition(w))
        if params.year_min:
            stmt = stmt.where(ListingSubmission.year >= params.year_min)
        if params.year_max:
            stmt = stmt.where(ListingSubmission.year <= params.year_max)
        if params.budget_min_usd:
            stmt = stmt.where(ListingSubmission.price_ask_usd >= params.budget_min_usd)
        if params.budget_max_usd:
            stmt = stmt.where(ListingSubmission.price_ask_usd <= params.budget_max_usd)
        if params.max_mileage_km:
            stmt = stmt.where(ListingSubmission.mileage <= params.max_mileage_km)
        stmt = stmt.order_by(ListingSubmission.created_at.desc()).limit(params.limit)
        r = await self.session.execute(stmt)
        return list(r.scalars().all())

    async def search(self, params: CarSearch) -> list[dict[str, Any]]:
        words = [w for w in (params.query or "").replace(",", " ").split() if w]
        rows = await self._run(params, words)
        if not rows and len(words) > 1:
            # «Malibu 2», «Nexia 3 2019» kabi so‘rovlarda qo‘shimcha raqam modelda bo‘lmasligi mumkin.
            rows = await self._run(params, [max(words, key=len)])
        return [car_to_dict(s) for s in rows]

    async def get(self, listing_id: int) -> dict[str, Any] | None:
        r = await self.session.execute(self._base().where(ListingSubmission.id == listing_id))
        sub = r.scalar_one_or_none()
        return car_to_dict(sub) if sub else None


class DbLeadSink:
    """Leadni bazaga yozadi va menejerlarga Telegramda «🔥 issiq mijoz» xabarini yuboradi."""

    def __init__(self, session: AsyncSession, bot: Bot, conversation_id: int | None) -> None:
        self.session = session
        self.bot = bot
        self.conversation_id = conversation_id

    async def save(self, client: ClientContext, lead: LeadInput) -> int:
        listing = None
        if lead.listing_id:
            listing = await self.session.get(ListingSubmission, lead.listing_id)
        car_text = lead.car_text
        if listing is not None and not car_text:
            car_text = f"{listing.brand} {listing.model}, {listing.year}"

        since = datetime.now(timezone.utc) - LEAD_DEDUP_WINDOW
        r = await self.session.execute(
            select(Lead)
            .where(
                Lead.platform == client.platform,
                Lead.client_social_id == client.social_id,
                Lead.phone == lead.phone,
                Lead.status == LeadStatus.NEW.value,
                Lead.created_at >= since,
            )
            .order_by(Lead.id.desc())
            .limit(1)
        )
        existing = r.scalar_one_or_none()
        if existing is not None:
            same_car = existing.listing_submission_id == (listing.id if listing else None)
            existing.client_name = lead.name or existing.client_name
            existing.listing_submission_id = listing.id if listing else existing.listing_submission_id
            existing.car_text = car_text or existing.car_text
            existing.payment_type = lead.payment_type or existing.payment_type
            existing.budget_usd = lead.budget_usd or existing.budget_usd
            existing.note = lead.note or existing.note
            await self.session.flush()
            if not same_car:
                await self._notify(existing, listing, client, updated=True)
            return existing.id

        row = Lead(
            platform=client.platform,
            client_social_id=client.social_id,
            client_name=lead.name or client.name,
            client_username=client.username,
            phone=lead.phone,
            listing_submission_id=listing.id if listing else None,
            car_text=car_text,
            payment_type=lead.payment_type,
            budget_usd=lead.budget_usd,
            note=lead.note,
            status=LeadStatus.NEW.value,
            conversation_id=self.conversation_id,
        )
        self.session.add(row)
        await self.session.flush()

        if client.platform == "telegram" and client.social_id.isdigit():
            await CrmRepository(self.session).get_or_create_client(
                telegram_id=int(client.social_id),
                full_name=lead.name or client.name,
                phone=lead.phone,
            )

        await self._notify(row, listing, client, updated=False)
        return row.id

    async def _notify(
        self,
        lead: Lead,
        listing: ListingSubmission | None,
        client: ClientContext,
        *,
        updated: bool,
    ) -> None:
        text = format_lead_notification(lead, listing, client, updated=updated)
        targets: list[int | str] = list(settings.admin_telegram_ids)
        if settings.leads_chat_id:
            targets.append(settings.leads_chat_id)
        for chat_id in targets:
            try:
                await self.bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                logger.exception("Lead xabarini yuborib bo‘lmadi: chat=%s lead=%s", chat_id, lead.id)


def format_lead_notification(
    lead: Lead,
    listing: ListingSubmission | None,
    client: ClientContext,
    *,
    updated: bool,
) -> str:
    esc = html.escape
    platform = {"telegram": "Telegram", "instagram": "Instagram"}.get(client.platform, client.platform)
    head = "♻️ <b>Issiq mijoz yangilandi</b>" if updated else "🔥 <b>Yangi issiq mijoz!</b>"
    lines = [f"{head} <i>(AI · {esc(platform)})</i>", ""]
    lines.append(f"👤 Ism: <b>{esc(lead.client_name or '—')}</b>")
    lines.append(f"📞 Tel: {esc(lead.phone)}")
    if listing is not None:
        car = f"{listing.brand} {listing.model}, {listing.year} — ${int(listing.price_ask_usd):,}"
        url = car_to_dict(listing).get("url")
        car_html = esc(car) + (f" · <a href=\"{esc(url, quote=True)}\">e'lon #{listing.id}</a>" if url else f" (#{listing.id})")
        lines.append(f"🚗 Mashina: {car_html}")
    elif lead.car_text:
        lines.append(f"🚗 Mashina: {esc(lead.car_text)}")
    if lead.payment_type:
        lines.append(f"💳 To‘lov: {esc(lead.payment_type)}")
    if lead.budget_usd:
        lines.append(f"💰 Byudjet: ${int(lead.budget_usd):,}")
    if lead.note:
        lines.append(f"📝 Izoh: {esc(lead.note)}")
    if client.platform == "telegram" and client.social_id.isdigit():
        profile = f"<a href=\"tg://user?id={client.social_id}\">profil</a>"
        if client.username:
            profile += f" · @{esc(client.username)}"
        lines.append(f"💬 Telegram: {profile}")
    elif client.platform == "instagram" and client.username:
        lines.append(f"📸 Instagram: https://instagram.com/{esc(client.username)}")
    lines.append(f"\n🆔 Lead #{lead.id} — CRM «Issiq mijozlar» bo‘limida.")
    return "\n".join(lines)


class ConversationStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _find(self, client: ClientContext) -> AiConversation | None:
        r = await self.session.execute(
            select(AiConversation).where(
                AiConversation.platform == client.platform,
                AiConversation.client_social_id == client.social_id,
            )
        )
        return r.scalar_one_or_none()

    async def get_or_create(self, client: ClientContext) -> AiConversation:
        conv = await self._find(client)
        if conv is not None:
            return conv
        # Parallel birinchi xabarlar uchun: ON CONFLICT — ikkinchisi mavjud qatorni oladi.
        await self.session.execute(
            pg_insert(AiConversation)
            .values(
                platform=client.platform,
                client_social_id=client.social_id,
                client_name=client.name,
                client_username=client.username,
                message_history=[],
                messages_count=0,
            )
            .on_conflict_do_nothing(constraint="uq_ai_conversations_platform_client")
        )
        conv = await self._find(client)
        assert conv is not None
        return conv

    async def append(self, conv: AiConversation, client: ClientContext, user_text: str, reply: str) -> None:
        history = list(conv.message_history or [])
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        conv.message_history = history[-STORED_HISTORY_MESSAGES:]
        conv.messages_count = (conv.messages_count or 0) + 1
        conv.client_name = client.name or conv.client_name
        conv.client_username = client.username or conv.client_username
        conv.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def reset(self, client: ClientContext) -> None:
        conv = await self.get_or_create(client)
        conv.message_history = []
        await self.session.flush()


class _DailyLimiter:
    """Bir mijoz kuniga nechta AI javob olishi mumkin (API xarajatini nazorat qilish)."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], tuple[date, int]] = {}

    def hit(self, key: tuple[str, str], limit: int) -> bool:
        today = date.today()
        day, count = self._counts.get(key, (today, 0))
        if day != today:
            count = 0
        if limit > 0 and count >= limit:
            return False
        self._counts[key] = (today, count + 1)
        if len(self._counts) > 50_000:
            self._counts = {k: v for k, v in self._counts.items() if v[0] == today}
        return True


_limiter = _DailyLimiter()
_llm: OpenAICompatibleClient | None = None


def _get_llm() -> OpenAICompatibleClient:
    global _llm
    if _llm is None:
        _llm = OpenAICompatibleClient(
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            base_url=settings.ai_base_url,
        )
    return _llm


async def close_ai_client() -> None:
    if _llm is not None:
        await _llm.close()


def fallback_text() -> str:
    from bot.config import sales_phone_entries

    phones = ", ".join(sales_phone_entries())
    return (
        "Kechirasiz, hozir javob berishda muammo bo‘ldi. 🙏\n"
        f"Menejerimizga to‘g‘ridan-to‘g‘ri qo‘ng‘iroq qiling: {phones}"
    )


def limit_text() -> str:
    from bot.config import sales_phone_entries

    return (
        "Bugungi savollar limiti tugadi. Batafsil ma’lumot uchun menejerimizga qo‘ng‘iroq qiling: "
        + ", ".join(sales_phone_entries())
    )


async def handle_ai_message(
    *,
    session: AsyncSession,
    bot: Bot,
    client: ClientContext,
    text: str,
) -> AssistantReply:
    text = (text or "").strip()[:2000]
    if not text:
        return AssistantReply(text="")
    if not _limiter.hit((client.platform, client.social_id), settings.ai_daily_limit):
        return AssistantReply(text=limit_text(), failed=True)

    store = ConversationStore(session)
    conv = await store.get_or_create(client)
    assistant = SalesAssistant(
        client=_get_llm(),
        catalog=DbCarCatalog(session),
        leads=DbLeadSink(session, bot, conv.id),
        business_info=settings.ai_business_info,
        fallback_text=fallback_text(),
    )
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in (conv.message_history or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    reply = await assistant.reply(client_ctx=client, history=history, user_text=text)
    if not reply.failed:
        await store.append(conv, client, text, reply.text)
    return reply
