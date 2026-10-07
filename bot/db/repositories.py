import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, literal, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    AppMeta,
    Client,
    ListingSubmission,
    ListingSubmissionStatus,
    ListingThread,
    ListingThreadMessage,
    User,
    Wishlist,
)


def _brand_synonyms_lower(brand: str) -> frozenset[str]:
    """Chevrolet/Daewoo (Nexia, Damas, Gentra…) uchun marka nomlari ekvivalent."""
    b = (brand or "").strip().lower()
    if not b:
        return frozenset()
    s = {b}
    if b in ("chevrolet", "daewoo"):
        s.update({"chevrolet", "daewoo"})
    return frozenset(s)


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

    async def count_users(self) -> int:
        c = await self.session.scalar(select(func.count()).select_from(User))
        return int(c or 0)

    async def rank_position(self, user: User) -> int:
        """Umumiy tartib: referrals_count desc, id asc (kanal TOP bilan bir xil)."""
        c = await self.session.scalar(
            select(func.count()).select_from(User).where(
                or_(
                    User.referrals_count > user.referrals_count,
                    and_(User.referrals_count == user.referrals_count, User.id < user.id),
                )
            )
        )
        return int(c or 0) + 1

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


class CrmRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_client_by_id(self, client_id: int) -> Client | None:
        r = await self.session.execute(select(Client).where(Client.id == client_id))
        return r.scalar_one_or_none()

    async def get_or_create_client(
        self,
        *,
        telegram_id: int,
        full_name: str | None = None,
        phone: str | None = None,
    ) -> Client:
        r = await self.session.execute(select(Client).where(Client.telegram_id == telegram_id))
        client = r.scalar_one_or_none()
        if client is None:
            client = Client(telegram_id=telegram_id, full_name=full_name, phone=phone)
            self.session.add(client)
            await self.session.flush()
            return client

        if full_name:
            client.full_name = full_name
        if phone:
            client.phone = phone
        await self.session.flush()
        return client

    async def create_listing_submission(
        self,
        *,
        client_id: int,
        user_telegram_id: int,
        seller_username: str | None = None,
        brand: str,
        model: str,
        year: int,
        mileage: int,
        condition_key: str,
        has_accident: bool,
        price_ask_usd: int,
        paint_status: str,
        extra_details: str = "",
        location: str | None = None,
        phone: str,
        photo_file_ids: list[str],
        payment_screenshot_file_id: str | None = None,
        payment_screenshot_unique_id: str | None = None,
    ) -> ListingSubmission:

        row = ListingSubmission(
            client_id=client_id,
            user_telegram_id=user_telegram_id,
            seller_username=(
                (seller_username or "").strip().lstrip("@")[:64] or None
            ),
            brand=brand,
            model=model,
            year=year,
            mileage=mileage,
            condition_key=condition_key,
            has_accident=has_accident,
            price_ask_usd=price_ask_usd,
            paint_status=paint_status,
            extra_details=extra_details or "",
            location=location,
            phone=phone,

            photo_file_ids=photo_file_ids,
            payment_screenshot_file_id=payment_screenshot_file_id,
            payment_screenshot_unique_id=(payment_screenshot_unique_id or None),
            status=ListingSubmissionStatus.PENDING,
        )
        self.session.add(row)
        await self.session.flush()
        return row


    async def get_listing_submission(
        self, listing_id: int, *, for_update: bool = False
    ) -> ListingSubmission | None:
        stmt = select(ListingSubmission).where(ListingSubmission.id == listing_id)
        if for_update:
            # Qatorni tranzaksiya oxirigacha qulflash: ikki admin / ikki bosish bir vaqtda
            # bir xil holatni o'zgartira olmasin. populate_existing — sessiyadagi eski nusxani yangilash.
            # Avval flush: chaqiruvchi saqlamagan o'zgarishlar (masalan buyout_status) o'chib ketmasin.
            await self.session.flush()
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        r = await self.session.execute(stmt)
        return r.scalar_one_or_none()

    async def lock_pending_listing(self, listing_id: int) -> ListingSubmission | None:
        """PENDING e'lonni qulflab qaytaradi (boshqa admin commit qilguncha kutadi)."""
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None or sub.status != ListingSubmissionStatus.PENDING:
            return None
        return sub

    async def acquire_user_submit_lock(self, user_telegram_id: int) -> None:
        """Bitta foydalanuvchining parallel «Tasdiqlash» bosishlarini ketma-ket qiladi (tranzaksiya oxirigacha)."""
        key = (0x5241 << 48) | (int(user_telegram_id) & 0xFFFF_FFFF_FFFF)
        await self.session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": key})

    async def listings_with_payment_screenshot(
        self, unique_id: str, *, exclude_id: int | None = None, limit: int = 5
    ) -> list[ListingSubmission]:
        """Shu to'lov skrinshoti ishlatilgan boshqa e'lonlar (firibgarlikni aniqlash)."""
        if not unique_id:
            return []
        stmt = select(ListingSubmission).where(ListingSubmission.payment_screenshot_unique_id == unique_id)
        if exclude_id is not None:
            stmt = stmt.where(ListingSubmission.id != exclude_id)
        r = await self.session.execute(stmt.order_by(ListingSubmission.id.desc()).limit(limit))
        return list(r.scalars().all())

    async def list_pending_listings(self, *, limit: int = 10) -> list[ListingSubmission]:
        r = await self.session.execute(
            select(ListingSubmission)
            .where(ListingSubmission.status == ListingSubmissionStatus.PENDING)
            .order_by(ListingSubmission.created_at.asc())
            .limit(limit)
        )
        return list(r.scalars().all())

    async def admin_stats(self) -> dict[str, int]:
        """Admin /stats uchun asosiy ko'rsatkichlar (bitta so'rov)."""
        r = await self.session.execute(
            text(
                """
                select
                  (select count(*) from users) as users,
                  (select count(*) from clients) as clients,
                  (select count(*) from listing_submissions where lower(status::text) = 'pending') as pending,
                  (select count(*) from listing_submissions where lower(status::text) = 'approved') as approved,
                  (select count(*) from listing_submissions where lower(status::text) = 'rejected') as rejected,
                  (select count(*) from listing_submissions
                     where lower(status::text) = 'approved' and listing_approved_at >= now() - interval '24 hours')
                     as approved_24h,
                  (select count(*) from listing_submissions where created_at >= now() - interval '24 hours')
                     as submitted_24h,
                  (select count(*) from listing_submissions where sale_status = 'sold') as sold,
                  (select count(*) from wishlist where is_active) as wishlists_active,
                  (select count(*) from listing_threads) as threads
                """
            )
        )
        return {k: int(v or 0) for k, v in r.mappings().one().items()}

    async def find_recent_duplicate_listing(
        self,
        *,
        user_telegram_id: int,
        photo_file_ids: list[str],
        within: timedelta = timedelta(minutes=30),
    ) -> ListingSubmission | None:
        """Xuddi shu rasmlar bilan yaqinda yuborilgan PENDING e'lon (ikki marta bosish / qayta yuborish)."""
        since = datetime.now(timezone.utc) - within
        r = await self.session.execute(
            select(ListingSubmission)
            .where(
                ListingSubmission.user_telegram_id == user_telegram_id,
                ListingSubmission.status == ListingSubmissionStatus.PENDING,
                ListingSubmission.created_at >= since,
            )
            .order_by(ListingSubmission.id.desc())
            .limit(10)
        )
        wanted = list(photo_file_ids)
        for row in r.scalars().all():
            if list(row.photo_file_ids or []) == wanted:
                return row
        return None

    async def try_mark_listing_approved(
        self,
        listing_id: int,
        *,
        channel_message_id: int | None,
    ) -> ListingSubmission | None:
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None or sub.status != ListingSubmissionStatus.PENDING:
            return None
        sub.status = ListingSubmissionStatus.APPROVED
        sub.channel_message_id = channel_message_id
        sub.listing_approved_at = datetime.now(timezone.utc)
        sub.sale_status = "open"
        sub.sale_last_prompt_at = None
        await self.session.flush()
        return sub

    async def listings_due_for_auto_publish(self, *, now: datetime, limit: int = 10) -> list[ListingSubmission]:
        """Muzlatish muddati tugagan, hech kim hal qilmagan e'lonlar (sotib olish taklifiga javob kelmaganlar ham)."""
        stmt = (
            select(ListingSubmission)
            .where(
                ListingSubmission.status == ListingSubmissionStatus.PENDING,
                ListingSubmission.frozen_until.is_not(None),
                ListingSubmission.frozen_until <= now,
                # «expired» ham — kanal xatosidan keyin qayta urinish uchun
                or_(
                    ListingSubmission.buyout_status.is_(None),
                    ListingSubmission.buyout_status.in_(("offered", "expired")),
                ),
            )
            .order_by(ListingSubmission.frozen_until.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def listings_with_stale_deals(self, *, now: datetime, limit: int = 10) -> list[ListingSubmission]:
        """Sotuvchi rozi bo'lgan / muhokamadagi, lekin jamoa hal qilmagan kelishuvlar (eslatma uchun)."""
        stmt = (
            select(ListingSubmission)
            .where(
                ListingSubmission.status == ListingSubmissionStatus.PENDING,
                ListingSubmission.buyout_status.in_(("accepted", "negotiating")),
                ListingSubmission.frozen_until.is_not(None),
                ListingSubmission.frozen_until <= now,
            )
            .order_by(ListingSubmission.frozen_until.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def try_mark_listing_rejected(
        self,
        listing_id: int,
        *,
        reason: str,
    ) -> ListingSubmission | None:
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None or sub.status != ListingSubmissionStatus.PENDING:
            return None
        sub.status = ListingSubmissionStatus.REJECTED
        sub.rejection_reason = reason
        await self.session.flush()
        return sub

    async def create_wishlist(
        self,
        *,
        client_id: int,
        brand: str,
        model: str | None,
        year_min: int,
        year_max: int,
        budget_min: int | None,
        budget_max: int,
        condition_key: str | None,
    ) -> Wishlist:
        row = Wishlist(
            client_id=client_id,
            brand=(brand or "").strip()[:100],
            model=(model or "").strip()[:100] or None,
            year_min=year_min,
            year_max=year_max,
            budget_min=budget_min,
            budget_max=budget_max,
            condition_key=(condition_key or "").strip()[:50] or None,
            is_active=True,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_wishlist(self, wishlist_id: int) -> Wishlist | None:
        r = await self.session.execute(select(Wishlist).where(Wishlist.id == wishlist_id))
        return r.scalar_one_or_none()

    async def deactivate_wishlist(self, wish: Wishlist) -> None:
        wish.is_active = False
        if wish.notified_at is None:
            wish.notified_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def count_active_wishlists(self, client_id: int) -> int:
        r = await self.session.execute(
            select(func.count(Wishlist.id)).where(
                Wishlist.client_id == client_id,
                Wishlist.is_active.is_(True),
            )
        )
        return int(r.scalar_one() or 0)

    async def list_active_wishlists(self, client_id: int, *, limit: int = 30) -> list[Wishlist]:
        r = await self.session.execute(
            select(Wishlist)
            .where(Wishlist.client_id == client_id, Wishlist.is_active.is_(True))
            .order_by(Wishlist.id.desc())
            .limit(limit)
        )
        return list(r.scalars().all())

    async def deactivate_wishlist_for_owner(self, wishlist_id: int, client_id: int) -> bool:
        r = await self.session.execute(
            select(Wishlist).where(
                Wishlist.id == wishlist_id,
                Wishlist.client_id == client_id,
                Wishlist.is_active.is_(True),
            )
        )
        w = r.scalar_one_or_none()
        if w is None:
            return False
        await self.deactivate_wishlist(w)
        return True

    async def mark_wishlist_notified(self, wish: Wishlist) -> None:
        """Oxirgi marta mos e'lon haqida xabar yuborilgan vaqt (qidiruv faol qoladi)."""
        wish.notified_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def find_wishlists_matching_listing(
        self,
        sub: ListingSubmission,
    ) -> list[tuple[Wishlist, Client]]:
        """E'lon (tasdiqlangan) qatoriga mos, faol wishlist + mijoz telegrami bor qatorlar."""
        return await self.find_wishlists_matching(
            brand=sub.brand,
            model=sub.model,
            year=sub.year,
            price_usd=sub.price_ask_usd,
            condition_key=sub.condition_key,
            exclude_client_id=sub.client_id,
        )

    async def find_wishlists_matching(
        self,
        *,
        brand: str | None,
        model: str | None,
        year: int,
        price_usd: int,
        condition_key: str | None = None,
        exclude_client_id: int | None = None,
    ) -> list[tuple[Wishlist, Client]]:
        """Mashina parametrlariga mos faol wishlistlar (bot e'loni ham, kanal mashinasi ham)."""
        lm = func.lower
        sb = (brand or "").strip()
        sm = (model or "").strip()
        brand_keys = _brand_synonyms_lower(sb) or frozenset({sb.lower()})
        brand_match = or_(*[lm(Wishlist.brand) == lm(literal(bk)) for bk in sorted(brand_keys)])
        wl_model = Wishlist.model
        lm_sm = lm(literal(sm))
        lm_wm = lm(wl_model)
        model_match = or_(
            wl_model.is_(None),
            and_(wl_model.is_not(None), lm_sm.like(func.concat("%", lm_wm, "%"))),
            and_(wl_model.is_not(None), lm_wm.like(func.concat("%", lm_sm, "%"))),
        )
        budget_hi = (Wishlist.budget_max * 11) // 10
        price_ok = and_(
            price_usd <= budget_hi,
            or_(Wishlist.budget_min.is_(None), price_usd >= Wishlist.budget_min),
        )
        # Kanal mashinasida holat kaliti yo'q — holat filtri faqat ma'lum bo'lsa qo'llanadi
        cond_ok = or_(
            Wishlist.condition_key.is_(None),
            Wishlist.condition_key == condition_key,
            literal(condition_key is None),
        )
        year_ok = and_(Wishlist.year_min <= year, Wishlist.year_max >= year)
        stmt = (
            select(Wishlist, Client)
            .join(Client, Client.id == Wishlist.client_id)
            .where(
                Wishlist.is_active.is_(True),
                Wishlist.client_id != (exclude_client_id or -1),
                Client.telegram_id.is_not(None),
                brand_match,
                model_match,
                year_ok,
                price_ok,
                cond_ok,
            )
        )
        r = await self.session.execute(stmt)
        return [(w, c) for w, c in r.all()]

    async def get_active_contest_row(self) -> dict | None:
        """Backend `contests` jadvalidagi oxirgi faol konkurs (bitta)."""
        r = await self.session.execute(
            text(
                """
                select id, title, prize, end_date, is_active
                from contests
                where is_active = true
                order by id desc
                limit 1
                """
            )
        )
        row = r.mappings().first()
        return dict(row) if row else None

    async def client_in_active_contest(self, contest_id: int, client_id: int) -> bool:
        r = await self.session.execute(
            text(
                """
                select 1 from contest_participants
                where contest_id = :cid and client_id = :clid
                limit 1
                """
            ),
            {"cid": contest_id, "clid": client_id},
        )
        return r.first() is not None

    async def join_active_contest(self, client_id: int) -> str:
        """`joined` | `already` | `none` | `ended` — rasmiy konkurs qatnashuvchilari ro‘yxati."""
        row = await self.get_active_contest_row()
        if not row:
            return "none"
        cid = int(row["id"])
        if await self.client_in_active_contest(cid, client_id):
            return "already"

        end = row.get("end_date")
        if end is not None:
            if isinstance(end, datetime):
                end_dt = end
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            else:
                raw = str(end).replace("Z", "+00:00")
                end_dt = datetime.fromisoformat(raw)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt < datetime.now(timezone.utc):
                return "ended"

        ins = await self.session.execute(
            text(
                """
                insert into contest_participants (contest_id, client_id)
                values (:c_id, :cl_id)
                on conflict (contest_id, client_id) do nothing
                returning id
                """
            ),
            {"c_id": cid, "cl_id": client_id},
        )
        if ins.scalar_one_or_none() is not None:
            return "joined"
        return "already"

    async def get_approved_listing(self, listing_id: int) -> ListingSubmission | None:
        r = await self.session.execute(
            select(ListingSubmission).where(
                ListingSubmission.id == listing_id,
                ListingSubmission.status == ListingSubmissionStatus.APPROVED,
            )
        )
        return r.scalar_one_or_none()

    async def get_or_create_listing_thread(
        self, listing_submission_id: int, buyer_telegram_id: int
    ) -> ListingThread:
        r = await self.session.execute(
            select(ListingThread).where(
                ListingThread.listing_submission_id == listing_submission_id,
                ListingThread.buyer_telegram_id == buyer_telegram_id,
            )
        )
        row = r.scalar_one_or_none()
        if row is not None:
            return row
        row = ListingThread(
            listing_submission_id=listing_submission_id,
            buyer_telegram_id=buyer_telegram_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def add_listing_thread_message(
        self,
        thread_id: int,
        *,
        is_from_seller: bool,
        body_text: str | None,
        voice_file_id: str | None,
        in_reply_to: int | None = None,
    ) -> ListingThreadMessage:
        bt = (body_text or "").strip() or None
        msg = ListingThreadMessage(
            thread_id=thread_id,
            is_from_seller=is_from_seller,
            body_text=bt,
            voice_file_id=(voice_file_id or None),
            in_reply_to=in_reply_to,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def get_thread_with_listing(self, thread_id: int) -> tuple[ListingThread, ListingSubmission] | None:
        r = await self.session.execute(select(ListingThread).where(ListingThread.id == thread_id))
        th = r.scalar_one_or_none()
        if th is None:
            return None
        sub = await self.get_listing_submission(th.listing_submission_id)
        if sub is None:
            return None
        return th, sub

    async def get_thread_message(self, message_id: int) -> ListingThreadMessage | None:
        r = await self.session.execute(select(ListingThreadMessage).where(ListingThreadMessage.id == message_id))
        return r.scalar_one_or_none()

    async def listings_due_for_sale_followup(
        self,
        *,
        now: datetime,
        interval: timedelta,
        first_after: timedelta,
        limit: int = 25,
    ) -> list[ListingSubmission]:
        """Takroriy so‘rov: `interval`dan keyin; birinchi so‘rov: tasdiqdan `first_after` o‘tgach."""
        due_repeat = now - interval
        due_first = now - first_after
        r = await self.session.execute(
            select(ListingSubmission)
            .where(
                ListingSubmission.status == ListingSubmissionStatus.APPROVED,
                ListingSubmission.channel_message_id.is_not(None),
                ListingSubmission.listing_approved_at.is_not(None),
                or_(
                    and_(
                        ListingSubmission.sale_status == "open",
                        or_(
                            and_(
                                ListingSubmission.sale_last_prompt_at.is_(None),
                                ListingSubmission.listing_approved_at <= due_first,
                            ),
                            and_(
                                ListingSubmission.sale_last_prompt_at.is_not(None),
                                ListingSubmission.sale_last_prompt_at <= due_repeat,
                            ),
                        ),
                    ),
                    # «Sotildi» bosilgan, lekin sharh kelmagan (masalan /start bosib chiqib ketgan) —
                    # aks holda e'lon abadiy feedback_pending da qolib, boshqa so'ralmaydi.
                    and_(
                        ListingSubmission.sale_status == "feedback_pending",
                        ListingSubmission.updated_at <= due_repeat,
                    ),
                ),
            )
            .order_by(ListingSubmission.listing_approved_at.asc())
            .limit(limit)
        )
        return list(r.scalars().all())

    async def mark_sale_prompt_sent(self, listing_id: int) -> None:
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None:
            return
        sub.sale_last_prompt_at = datetime.now(timezone.utc)
        if sub.sale_status == "feedback_pending":
            # Yangi so'rov tugmalari ishlashi uchun (try_set_sale_* «open» kutadi).
            sub.sale_status = "open"
        await self.session.flush()

    async def try_set_sale_feedback_pending(self, listing_id: int, *, user_telegram_id: int) -> ListingSubmission | None:
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None or sub.user_telegram_id != user_telegram_id:
            return None
        if sub.status != ListingSubmissionStatus.APPROVED or sub.sale_status != "open":
            return None
        sub.sale_status = "feedback_pending"
        await self.session.flush()
        return sub

    async def try_revert_sale_feedback_pending(self, listing_id: int, *, user_telegram_id: int) -> ListingSubmission | None:
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None or sub.user_telegram_id != user_telegram_id:
            return None
        if sub.sale_status != "feedback_pending":
            return None
        sub.sale_status = "open"
        await self.session.flush()
        return sub

    async def try_set_sale_not_sold(self, listing_id: int, *, user_telegram_id: int) -> ListingSubmission | None:
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None or sub.user_telegram_id != user_telegram_id:
            return None
        if sub.status != ListingSubmissionStatus.APPROVED or sub.sale_status != "open":
            return None
        sub.sale_status = "not_sold"
        await self.session.flush()
        return sub

    async def try_finalize_sale_sold(self, listing_id: int, *, user_telegram_id: int) -> ListingSubmission | None:
        sub = await self.get_listing_submission(listing_id, for_update=True)
        if sub is None or sub.user_telegram_id != user_telegram_id:
            return None
        if sub.status != ListingSubmissionStatus.APPROVED or sub.sale_status != "feedback_pending":
            return None
        sub.sale_status = "sold"
        await self.session.flush()
        return sub
