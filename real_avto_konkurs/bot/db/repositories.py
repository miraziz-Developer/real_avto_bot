import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AppMeta, User


def _gen_referral_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        r = await self.session.execute(select(User).where(User.tg_id == tg_id))
        return r.scalar_one_or_none()

    async def get_by_referral_code(self, code: str) -> User | None:
        r = await self.session.execute(select(User).where(User.referral_code == code.upper()))
        return r.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        r = await self.session.execute(select(User).where(User.id == user_id))
        return r.scalar_one_or_none()

    async def create_user(
        self,
        tg_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        referred_by_id: int | None,
    ) -> User:
        for _ in range(20):
            code = _gen_referral_code()
            exists = await self.get_by_referral_code(code)
            if exists is None:
                break
        else:
            raise RuntimeError("referral_code generatsiyasi muvaffaqiyatsiz")

        user = User(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            referral_code=code,
            referred_by_id=referred_by_id,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def ensure_user(
        self,
        tg_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        start_ref_code: str | None,
    ) -> User:
        existing = await self.get_by_tg_id(tg_id)
        if existing:
            await self._maybe_attach_referrer(existing, start_ref_code)
            if username is not None:
                existing.username = username
            if first_name is not None:
                existing.first_name = first_name
            if last_name is not None:
                existing.last_name = last_name
            await self.session.flush()
            return existing

        referred_by_id: int | None = None
        if start_ref_code:
            ref = await self.get_by_referral_code(start_ref_code.strip().upper())
            if ref and ref.tg_id != tg_id:
                referred_by_id = ref.id

        return await self.create_user(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            referred_by_id=referred_by_id,
        )

    async def _maybe_attach_referrer(self, user: User, start_ref_code: str | None) -> None:
        if not start_ref_code or user.referred_by_id is not None or user.referral_credited:
            return
        ref = await self.get_by_referral_code(start_ref_code.strip().upper())
        if ref and ref.id != user.id and ref.tg_id != user.tg_id:
            user.referred_by_id = ref.id

    async def mark_channel_ok(self, user: User) -> None:
        user.channel_ok = True
        await self.session.flush()
        await self._try_credit_referral(user)

    async def mark_instagram_ok(self, user: User) -> None:
        user.instagram_ok = True
        await self.session.flush()
        await self._try_credit_referral(user)

    async def _try_credit_referral(self, user: User) -> None:
        if not (user.channel_ok and user.instagram_ok):
            return
        if user.referral_credited or user.referred_by_id is None:
            return

        referrer = await self.get_by_id(user.referred_by_id)
        if referrer is None or referrer.tg_id == user.tg_id:
            user.referred_by_id = None
            await self.session.flush()
            return

        user.referral_credited = True
        await self.session.execute(
            update(User)
            .where(User.id == referrer.id)
            .values(referrals_count=User.referrals_count + 1)
        )
        await self.session.flush()

    async def top_referrers(self, limit: int = 15) -> list[User]:
        r = await self.session.execute(
            select(User)
            .where(User.referrals_count > 0)
            .order_by(User.referrals_count.desc(), User.id.asc())
            .limit(limit)
        )
        return list(r.scalars().all())

    async def set_leaderboard_alias(self, user: User, alias: str | None) -> None:
        user.leaderboard_alias = alias
        await self.session.flush()


class AppMetaRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, key: str) -> str | None:
        r = await self.session.execute(select(AppMeta.value).where(AppMeta.key == key))
        return r.scalar_one_or_none()

    async def set(self, key: str, value: str) -> None:
        existing = await self.session.execute(select(AppMeta).where(AppMeta.key == key))
        row = existing.scalar_one_or_none()
        if row:
            row.value = value
        else:
            self.session.add(AppMeta(key=key, value=value))
        await self.session.flush()

    async def get_next_leaderboard_at(self) -> datetime | None:
        raw = await self.get("next_leaderboard_at")
        if not raw:
            return None
        return datetime.fromisoformat(raw)

    async def set_next_leaderboard_at(self, dt: datetime) -> None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        await self.set("next_leaderboard_at", dt.isoformat())
