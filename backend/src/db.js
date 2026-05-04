import pg from "pg";
import dotenv from "dotenv";

dotenv.config();

const { Pool } = pg;

const max = Math.max(2, Number(process.env.PG_POOL_MAX || 20));

export const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 15_000,
  allowExitOnIdle: false,
});
