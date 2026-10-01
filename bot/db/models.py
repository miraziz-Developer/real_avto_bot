from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    referred_by_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    referral_credited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    referrals_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)

    channel_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    instagram_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Kanaldagi TOP uchun ko‘rinadigan taxallus (ixtiyoriy, lekin tavsiya)
    leaderboard_alias: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    referred_by: Mapped["User | None"] = relationship(
        "User",
        remote_side=[id],
        foreign_keys=[referred_by_id],
    )


class AppMeta(Base):
    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class ClientSource(StrEnum):
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    PHONE = "phone"
    OFFICE = "office"


class ClientStatus(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    VIP = "vip"
    BLOCKED = "blocked"


class CarCondition(StrEnum):
    IDEAL = "ideal"
    YAXSHI = "yaxshi"
    QONIQARLI = "qoniqarli"
    TAMIR = "tamir"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[ClientSource] = mapped_column(
        Enum(ClientSource, name="client_source_enum"),
        default=ClientSource.TELEGRAM,
        nullable=False,
    )
    status: Mapped[ClientStatus] = mapped_column(
        Enum(ClientStatus, name="client_status_enum"),
        default=ClientStatus.NEW,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ListingSubmissionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Wishlist(Base):
    """Foydalanuvchi qidiruv parametrlari: kanalga yangi e'lon mos kelsa xabar."""

    __tablename__ = "wishlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    year_min: Mapped[int] = mapped_column(Integer, nullable=False)
    year_max: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_min: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # USD, butun son
    budget_max: Mapped[int] = mapped_column(BigInteger, nullable=False)  # USD, butun son
    condition_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ListingThread(Base):
    """E'lon bo'yicha xaridor ↔ sotuvchi (faqat bot orqali, anonim)."""

    __tablename__ = "listing_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_submission_id: Mapped[int] = mapped_column(
        ForeignKey("listing_submissions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    buyer_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "listing_submission_id",
            "buyer_telegram_id",
            name="uq_listing_threads_listing_buyer",
        ),
    )


class ListingThreadMessage(Base):
    __tablename__ = "listing_thread_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("listing_threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    is_from_seller: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    in_reply_to: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("listing_thread_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ListingSubmission(Base):
    """Foydalanuvchi e'lonlari: admin tasdiqlaguncha kutish, keyin kanalga post."""

    __tablename__ = "listing_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    seller_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[ListingSubmissionStatus] = mapped_column(
        Enum(ListingSubmissionStatus, name="listing_submission_status_enum"),
        default=ListingSubmissionStatus.PENDING,
        nullable=False,
        index=True,
    )
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    mileage: Mapped[int] = mapped_column(Integer, nullable=False)
    condition_key: Mapped[str] = mapped_column(String(32), nullable=False)
    has_accident: Mapped[bool] = mapped_column(Boolean, nullable=False)
    price_ask_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    paint_status: Mapped[str] = mapped_column(String(120), nullable=False)
    extra_details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)

    photo_file_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    payment_screenshot_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    channel_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Tasdiqlangan e'lon: sotilish holati (Telegramdan so‘rov + kanalda «SOTILDI»).
    listing_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # open | feedback_pending | sold | not_sold | None (pending/rejected e'lonlar)
    sale_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    sale_last_prompt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class CarStatus(StrEnum):
    REVIEW = "review"  # AI ma'lumotni to'liq ajrata olmadi — admin tekshiruvi kerak
    ACTIVE = "active"  # sotuvda
    RESERVED = "reserved"  # bron qilingan
    SOLD = "sold"
    ARCHIVED = "archived"  # e'lon emas / o'chirilgan


class CarSource(StrEnum):
    CHANNEL = "channel"  # jamoa kanalga o'zi tashlagan post
    BOT = "bot"  # foydalanuvchi bot orqali bergan e'lon (listing_submissions)
    ADMIN = "admin"
    IMPORT = "import"  # Telegram Desktop eksportidan


class Car(Base):
    """Bitta mashina — bitta yozuv, qayerdan kelganidan qat'i nazar. Savdo agenti shu jadvaldan javob beradi."""

    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(20), default=CarStatus.REVIEW, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), default=CarSource.CHANNEL, nullable=False, index=True)

    brand: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    mileage_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    transmission: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fuel: Mapped[str | None] = mapped_column(String(30), nullable=True)
    position: Mapped[str | None] = mapped_column(String(30), nullable=True)
    paint_status: Mapped[str | None] = mapped_column(String(200), nullable=True)
    has_accident: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # AI qisqa xulosasi (holat, kamchiliklar)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")  # post matni + ovoz transkripti
    photo_file_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    video_file_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)

    channel_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Albomdagi barcha xabarlar (tahrir/reply qaysi biriga kelsa ham topish uchun)
    channel_message_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False, default=list)
    listing_submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("listing_submissions.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    # Biz o'zimiz sotib olgan mashina bo'lsa — foyda hisobi uchun
    is_own: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purchase_price_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expenses_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sold_price_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    stale_prompted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def title(self) -> str:
        name = " ".join(p for p in (self.brand, self.model) if p) or "Mashina"
        return f"{name} {self.year}" if self.year else name


class CarEvent(Base):
    """Mashina tarixi: yaratildi, narx o'zgardi, status o'zgardi — statistika va narx tarixi shu yerdan."""

    __tablename__ = "car_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
