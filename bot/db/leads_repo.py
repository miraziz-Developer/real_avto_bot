"""Savdo leadlari, suhbat tarixi va admin↔mijoz relay."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import OPEN_LEAD_STATUSES, AgentMessage, BusinessConnection, Lead, LeadRelay, LeadStatus

# Suhbat tarixidan AI kontekstiga beriladigan oxirgi xabarlar soni
HISTORY_LIMIT = 20
# Shuncha vaqt jim qolgan ochiq lead yangi suhbatda «yangi» hisoblanadi (eski kontekst aralashmasin)
LEAD_IDLE_RESET = timedelta(days=14)


def _now() -> datetime:
    return datetime.now(UTC)


class LeadRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, lead_id: int) -> Lead | None:
        return await self.session.get(Lead, lead_id)

    async def get_open(self, telegram_id: int) -> Lead | None:
        stmt = (
            select(Lead)
            .where(Lead.telegram_id == telegram_id, Lead.status.in_(OPEN_LEAD_STATUSES))
            .order_by(Lead.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_create_open(
        self,
        telegram_id: int,
        *,
        name: str | None = None,
        username: str | None = None,
        client_id: int | None = None,
        channel: str = "bot",
    ) -> tuple[Lead, bool]:
        # Mijoz ketma-ket ikki xabar yuborsa (aiogram ularni parallel ishlaydi) — ikkita lead ochilmasin
        await self.session.execute(
            text("select pg_advisory_xact_lock(:ns, hashtext(:id))"), {"ns": 0x4C45, "id": str(telegram_id)}
        )
        lead = await self.get_open(telegram_id)
        if lead is not None:
            idle = lead.last_message_at and _now() - lead.last_message_at > LEAD_IDLE_RESET
            if not (idle and lead.status == LeadStatus.ACTIVE):
                if username and lead.username != username:
                    lead.username = username
                return lead, False
            # Uzoq jim qolgan AI suhbati — yopib, yangisini boshlaymiz
            await self.close(lead, LeadStatus.LOST)
        lead = Lead(
            telegram_id=telegram_id,
            name=name,
            username=username,
            client_id=client_id,
            channel=channel,
            status=LeadStatus.ACTIVE,
        )
        self.session.add(lead)
        await self.session.flush()
        return lead, True

    async def add_message(self, lead: Lead, role: str, content: str) -> AgentMessage:
        msg = AgentMessage(lead_id=lead.id, role=role, content=content[:4000])
        self.session.add(msg)
        lead.last_message_at = _now()
        await self.session.flush()
        return msg

    async def history(self, lead: Lead, *, limit: int = HISTORY_LIMIT) -> list[AgentMessage]:
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.lead_id == lead.id)
            .order_by(AgentMessage.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        rows.reverse()
        return rows

    async def hand_off(self, lead: Lead, *, reason: str, summary: str | None) -> bool:
        """AI → admin. Qayta topshirish (allaqachon topshirilgan/odam olgan) bo'lsa False."""
        lead.summary = summary or lead.summary
        lead.handoff_reason = reason[:300]
        if lead.status != LeadStatus.ACTIVE:
            return False
        lead.status = LeadStatus.HANDED_OFF
        lead.handed_off_at = _now()
        lead.reminded_at = None
        return True

    async def take(self, lead: Lead, admin_id: int) -> bool:
        """Admin «Oldim» — AI jim turadi. Boshqa admin oldin olgan bo'lsa False."""
        # Qatorni qulflab yangilaymiz: ikki admin bir vaqtda bossa ikkinchisi birinchisining natijasini ko'radi
        await self.session.flush()
        await self.session.refresh(lead, with_for_update=True)
        if lead.assigned_admin_id and lead.assigned_admin_id != admin_id and lead.human_mode:
            return False
        lead.assigned_admin_id = admin_id
        lead.human_mode = True
        lead.status = LeadStatus.IN_PROGRESS
        return True

    async def back_to_ai(self, lead: Lead) -> None:
        lead.human_mode = False
        lead.human_until = None
        lead.status = LeadStatus.ACTIVE

    @staticmethod
    def is_human(lead: Lead) -> bool:
        """AI jim turishi kerakmi: admin olgan yoki business egasi yaqinda o'zi yozgan."""
        if lead.human_mode:
            return True
        return bool(lead.human_until and lead.human_until > _now())

    async def pause_for_owner(self, lead: Lead, *, hours: int) -> None:
        """Business akkaunt egasi mijozga o'zi yozdi — AI vaqtincha jim."""
        lead.human_until = _now() + timedelta(hours=max(1, hours))

    async def upsert_business_connection(
        self,
        *,
        connection_id: str,
        owner_user_id: int,
        owner_name: str | None,
        can_reply: bool,
        is_enabled: bool,
    ) -> BusinessConnection:
        row = await self.session.get(BusinessConnection, connection_id)
        if row is None:
            row = BusinessConnection(id=connection_id, owner_user_id=owner_user_id)
            self.session.add(row)
        row.owner_user_id = owner_user_id
        row.owner_name = owner_name
        row.can_reply = can_reply
        row.is_enabled = is_enabled
        await self.session.flush()
        return row

    async def get_business_connection(self, connection_id: str) -> BusinessConnection | None:
        return await self.session.get(BusinessConnection, connection_id)

    async def close(self, lead: Lead, status: str) -> None:
        lead.status = status
        lead.human_mode = False
        lead.closed_at = _now()

    async def add_relay(self, lead: Lead, admin_chat_id: int, admin_message_id: int) -> None:
        self.session.add(LeadRelay(lead_id=lead.id, admin_chat_id=admin_chat_id, admin_message_id=admin_message_id))
        await self.session.flush()

    async def lead_by_relay(self, admin_chat_id: int, admin_message_id: int) -> Lead | None:
        stmt = (
            select(Lead)
            .join(LeadRelay, LeadRelay.lead_id == Lead.id)
            .where(LeadRelay.admin_chat_id == admin_chat_id, LeadRelay.admin_message_id == admin_message_id)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_open(self, *, limit: int = 30) -> list[Lead]:
        stmt = (
            select(Lead)
            .where(Lead.status.in_(OPEN_LEAD_STATUSES))
            .order_by(Lead.score.desc(), Lead.last_message_at.desc().nulls_last())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def due_for_reminder(self, *, after_minutes: int, limit: int = 20) -> list[Lead]:
        cutoff = _now() - timedelta(minutes=max(1, after_minutes))
        stmt = (
            select(Lead)
            .where(
                Lead.status == LeadStatus.HANDED_OFF,
                Lead.assigned_admin_id.is_(None),
                Lead.handed_off_at < cutoff,
                Lead.reminded_at.is_(None),
            )
            .order_by(Lead.handed_off_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())
