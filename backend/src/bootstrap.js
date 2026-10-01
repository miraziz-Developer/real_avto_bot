import { pool } from "./db.js";
import { config } from "./config.js";
import { hashPassword, verifyPassword } from "./utils.js";

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
      password_hash varchar(128) not null,
      role varchar(20) not null default 'admin',
      is_active boolean not null default true,
      created_at timestamptz not null default now()
    );

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

  const username = config.adminUser;
  const existing = await pool.query("select password_hash from crm_users where username=$1", [username]);
  const current = existing.rows[0]?.password_hash;
  // .env dagi parol o'zgarmagan va xesh allaqachon scrypt bo'lsa — qayta yozmaymiz
  if (current && current.startsWith("scrypt$") && verifyPassword(config.adminPassword, current)) return;
  await pool.query(
    `insert into crm_users (username, password_hash, role)
     values ($1,$2,'admin')
     on conflict (username)
     do update set password_hash=excluded.password_hash`,
    [username, hashPassword(config.adminPassword)],
  );
}
