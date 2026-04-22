"""Mavjud PostgreSQL jadvaliga yangi ustunlar (create_all o‘zgartirmaydi)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def apply_user_leaderboard_alias_column(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE users ADD COLUMN leaderboard_alias VARCHAR(64);
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )
