from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
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
