from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(
    database_url: str,
    *,
    pool_size: int = 20,
    max_overflow: int = 40,
    pool_recycle: int = 3600,
    pool_timeout: int = 30,
) -> async_sessionmaker[AsyncSession]:
    global _engine, _session_factory
    if _engine is not None:
        return _session_factory  # type: ignore[return-value]

    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=max(1, pool_size),
        max_overflow=max(0, max_overflow),
        pool_recycle=max(300, pool_recycle),
        pool_timeout=max(5, pool_timeout),
        connect_args={"server_settings": {"application_name": "real_avto_bot"}},
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        autoflush=False,
    )
    return _session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("init_engine() chaqirilmagan")
    return _session_factory


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("init_engine() chaqirilmagan")
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def create_tables() -> None:
    if _engine is None:
        raise RuntimeError("init_engine() avval chaqirilishi kerak")
    import bot.db.models  # noqa: F401 — jadvallarni metadata ga ro‘yxatdan o‘tkazish
    from bot.db.models import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
