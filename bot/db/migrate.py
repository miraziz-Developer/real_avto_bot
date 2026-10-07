"""Mavjud PostgreSQL jadvaliga yangi ustunlar (create_all o‘zgartirmaydi)."""

import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def apply_listing_seller_username_column(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE listing_submissions
                        ADD COLUMN seller_username VARCHAR(64);
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )


async def apply_listing_extra_details_column(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE listing_submissions
                        ADD COLUMN extra_details TEXT NOT NULL DEFAULT '';
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )


def _usd_rate() -> int:
    return max(1, int((os.getenv("USD_RATE_UZS", "") or "13000").strip() or "13000"))


async def _claim_once(conn, key: str) -> bool:
    """Bir martalik ma'lumot migratsiyasi: True — shu chaqiruv bajarishi kerak (belgi shu tranzaksiyada qo'yiladi)."""
    await conn.execute(
        text("CREATE TABLE IF NOT EXISTS app_meta (key VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)")
    )
    row = (
        await conn.execute(
            text("INSERT INTO app_meta (key, value) VALUES (:k, 'done') ON CONFLICT (key) DO NOTHING RETURNING key"),
            {"k": key},
        )
    ).first()
    return row is not None


async def apply_listing_price_ask_usd_rename(engine: AsyncEngine) -> None:
    """Eski ustun nomi price_ask_uzs bo'lsa — price_ask_usd (qiymat: USD butun son)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'listing_submissions'
                          AND column_name = 'price_ask_uzs'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'listing_submissions'
                          AND column_name = 'price_ask_usd'
                    ) THEN
                        ALTER TABLE listing_submissions
                            RENAME COLUMN price_ask_uzs TO price_ask_usd;
                    END IF;
                END $$;
                """
            )
        )
        # Bir martalik (so'm → USD) konvertatsiya. Avval har restartda ishlardi va 1 mln dan katta har qanday
        # qiymatni qayta-qayta kursga bo'lib yuborardi — endi app_meta belgisi bilan faqat bir marta.
        if await _claim_once(conn, "migr:listing_price_uzs_to_usd"):
            rate = _usd_rate()
            await conn.execute(
                text(
                    f"""
                    UPDATE listing_submissions
                    SET price_ask_usd = GREATEST(1, (price_ask_usd / {rate})::bigint)
                    WHERE price_ask_usd > 1000000;
                    """
                )
            )


async def apply_wishlist_table(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wishlist (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    brand VARCHAR(100) NOT NULL,
                    model VARCHAR(100),
                    year_min INTEGER NOT NULL,
                    year_max INTEGER NOT NULL,
                    budget_min BIGINT,
                    budget_max BIGINT NOT NULL,
                    condition_key VARCHAR(50),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    notified_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_wishlist_client_active
                    ON wishlist (client_id) WHERE is_active = TRUE
                """
            )
        )
        if not await _claim_once(conn, "migr:wishlist_budget_uzs_to_usd"):
            return
        rate = _usd_rate()
        await conn.execute(
            text(
                f"""
                UPDATE wishlist
                SET
                    budget_max = GREATEST(1, (budget_max / {rate})::bigint),
                    budget_min = CASE
                        WHEN budget_min IS NULL THEN NULL
                        ELSE GREATEST(1, (budget_min / {rate})::bigint)
                    END
                WHERE budget_max > 1000000;
                """
            )
        )


async def apply_contest_tables(engine: AsyncEngine) -> None:
    """CRM/bot umumiy bazada konkurs jadvallari (backend bootstrap bilan mos)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS contests (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    prize VARCHAR(255) NOT NULL,
                    start_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    end_date TIMESTAMPTZ NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    winner_client_id INTEGER NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS contest_participants (
                    id SERIAL PRIMARY KEY,
                    contest_id INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    is_winner BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE(contest_id, client_id)
                )
                """
            )
        )


async def apply_listing_thread_tables(engine: AsyncEngine) -> None:
    """E'lon bo'yicha anonim savol-javob iplari."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS listing_threads (
                    id SERIAL PRIMARY KEY,
                    listing_submission_id INTEGER NOT NULL
                        REFERENCES listing_submissions(id) ON DELETE CASCADE,
                    buyer_telegram_id BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (listing_submission_id, buyer_telegram_id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_listing_threads_listing
                    ON listing_threads (listing_submission_id)
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS listing_thread_messages (
                    id SERIAL PRIMARY KEY,
                    thread_id INTEGER NOT NULL
                        REFERENCES listing_threads(id) ON DELETE CASCADE,
                    is_from_seller BOOLEAN NOT NULL DEFAULT FALSE,
                    body_text TEXT,
                    voice_file_id VARCHAR(256),
                    in_reply_to INTEGER
                        REFERENCES listing_thread_messages(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_listing_thread_messages_thread
                    ON listing_thread_messages (thread_id)
                """
            )
        )


async def apply_listing_sale_followup_columns(engine: AsyncEngine) -> None:
    """Tasdiqlangan e'lon uchun sotilish so‘rovi (24 soatlik tsikl) va kanalda «SOTILDI»."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE listing_submissions
                        ADD COLUMN listing_approved_at TIMESTAMPTZ;
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE listing_submissions
                        ADD COLUMN sale_status VARCHAR(20);
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE listing_submissions
                        ADD COLUMN sale_last_prompt_at TIMESTAMPTZ;
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE listing_submissions
                SET
                    listing_approved_at = NOW(),
                    sale_status = 'open',
                    sale_last_prompt_at = NOW()
                WHERE status::text = 'approved'
                  AND channel_message_id IS NOT NULL
                  AND listing_approved_at IS NULL;
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE listing_submissions
                SET
                    sale_status = 'open',
                    listing_approved_at = COALESCE(listing_approved_at, created_at, NOW())
                WHERE status::text = 'approved'
                  AND channel_message_id IS NOT NULL
                  AND (sale_status IS NULL OR sale_status = '');
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_listing_submissions_sale_status
                    ON listing_submissions (sale_status)
                    WHERE sale_status IS NOT NULL;
                """
            )
        )


async def apply_performance_indexes(engine: AsyncEngine) -> None:
    """Tez-tez ishlatiladigan so‘rovlar uchun indekslar (har biri alohida — asyncpg)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_listing_submissions_status_created
                    ON listing_submissions (status, created_at DESC)
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_listing_submissions_user_status
                    ON listing_submissions (user_telegram_id, status)
                """
            )
        )


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


async def apply_listing_payment_screenshot_column(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE listing_submissions
                        ADD COLUMN payment_screenshot_file_id VARCHAR(256);
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )


async def apply_listing_location_column(engine: AsyncEngine) -> None:
    """E'lon uchun hudud (location) ustuni."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TABLE listing_submissions
                        ADD COLUMN location VARCHAR(200);
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END $$;
                """
            )
        )




async def apply_listing_payment_unique_id_column(engine: AsyncEngine) -> None:
    """To'lov skrinshotining file_unique_id si (qayta ishlatishni aniqlash)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE listing_submissions
                    ADD COLUMN IF NOT EXISTS payment_screenshot_unique_id VARCHAR(64)
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_listing_submissions_payment_screenshot_unique_id
                    ON listing_submissions (payment_screenshot_unique_id)
                """
            )
        )


async def apply_car_indexes(engine: AsyncEngine) -> None:
    """Kanal tahriri/reply qaysi albom xabariga kelsa ham mashinani tez topish uchun GIN indeks."""
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_cars_channel_message_ids ON cars USING GIN (channel_message_ids)")
        )


async def apply_lead_business_columns(engine: AsyncEngine) -> None:
    """leads jadvali Telegram Business ustunlaridan oldin yaratilgan bo'lsa — qo'shish."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS human_until TIMESTAMPTZ"))
        await conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS business_connection_id VARCHAR(100)"))


async def apply_listing_freeze_columns(engine: AsyncEngine) -> None:
    """E'lon muzlatish va sotib olish taklifi ustunlari."""
    async with engine.begin() as conn:
        for ddl in (
            "ALTER TABLE listing_submissions ADD COLUMN IF NOT EXISTS frozen_until TIMESTAMPTZ",
            "ALTER TABLE listing_submissions ADD COLUMN IF NOT EXISTS buyout_status VARCHAR(20)",
            "ALTER TABLE listing_submissions ADD COLUMN IF NOT EXISTS buyout_price_usd BIGINT",
            "ALTER TABLE listing_submissions ADD COLUMN IF NOT EXISTS buyout_admin_id BIGINT",
            "ALTER TABLE listing_submissions ADD COLUMN IF NOT EXISTS buyout_offered_at TIMESTAMPTZ",
            "ALTER TABLE listing_submissions ADD COLUMN IF NOT EXISTS auto_published BOOLEAN NOT NULL DEFAULT FALSE",
            "CREATE INDEX IF NOT EXISTS ix_listing_submissions_frozen_until ON listing_submissions (frozen_until)",
        ):
            await conn.execute(text(ddl))
