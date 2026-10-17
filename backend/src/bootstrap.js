import { pool } from "./db.js";
import { config } from "./config.js";
import { hashPassword, isLegacyHash, verifyPassword } from "./password.js";

export async function bootstrapDatabase() {
  await pool.query(`
    create table if not exists clients (
      id serial primary key,
      telegram_id bigint unique,
      full_name varchar(200),
      phone varchar(20),
      source varchar(32),
      status varchar(32),
      notes text,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    );

    create table if not exists crm_users (
      id serial primary key,
      username varchar(80) unique not null,
      password_hash varchar(255) not null,
      role varchar(20) not null default 'admin',
      is_active boolean not null default true,
      created_at timestamptz not null default now()
    );

    alter table crm_users alter column password_hash type varchar(255);

    create table if not exists contests (
      id serial primary key,
      title varchar(255) not null,
      prize varchar(255) not null,
      start_date timestamptz not null default now(),
      end_date timestamptz not null,
      is_active boolean not null default true,
      winner_client_id integer null,
      created_at timestamptz not null default now()
    );

    create table if not exists contest_participants (
      id serial primary key,
      contest_id integer not null references contests(id) on delete cascade,
      client_id integer not null references clients(id) on delete cascade,
      joined_at timestamptz not null default now(),
      is_winner boolean not null default false,
      unique(contest_id, client_id)
    );
  `);

  await syncAdminUser();
}

/**
 * `.env` dagi CRM_ADMIN_USER / CRM_ADMIN_PASSWORD — admin parolining manbai.
 * Xesh faqat parol o'zgarganda yoki eski (sha256) formatda bo'lsa qayta yoziladi.
 */
async function syncAdminUser() {
  const username = config.adminUser;
  const r = await pool.query("select id, password_hash from crm_users where username=$1", [username]);
  const existing = r.rows[0];
  if (existing) {
    const same = await verifyPassword(config.adminPassword, existing.password_hash);
    if (same && !isLegacyHash(existing.password_hash)) return;
    await pool.query("update crm_users set password_hash=$1 where id=$2", [
      await hashPassword(config.adminPassword),
      existing.id,
    ]);
    return;
  }
  await pool.query(
    `insert into crm_users (username, password_hash, role)
     values ($1,$2,'admin')
     on conflict (username) do nothing`,
    [username, await hashPassword(config.adminPassword)],
  );
}
