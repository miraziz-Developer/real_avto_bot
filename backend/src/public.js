import crypto from "crypto";
import express from "express";
import { pool } from "./db.js";
import { asyncHandler } from "./utils.js";

/**
 * Ochiq katalog API (sayt va Telegram Mini App). Login yo'q — faqat sotuvdagi mashinalar.
 * Hech qachon: sotuvchi telefoni, xarid narxi, foyda, ichki izohlar.
 */
export const publicRouter = express.Router();

const BOT_TOKEN = (process.env.BOT_TOKEN || "").trim();
const OFFERABLE = ["active", "reserved"];
const PAGE_SIZE = 24;

// --- Oddiy IP rate-limit (ochiq API'ni ortiqcha so'rovdan himoya) ------------------------------
const hits = new Map();
const LIMIT_PER_MINUTE = Number.parseInt(process.env.PUBLIC_RATE_LIMIT || "120", 10) || 120;

publicRouter.use((req, res, next) => {
  const ip = req.ip || "?";
  const now = Date.now();
  const rec = hits.get(ip);
  if (!rec || now > rec.reset) {
    if (hits.size > 20000) hits.clear();
    hits.set(ip, { n: 1, reset: now + 60_000 });
    return next();
  }
  rec.n += 1;
  if (rec.n > LIMIT_PER_MINUTE) return res.status(429).json({ error: "too_many_requests" });
  return next();
});

// --- Narx bahosi («Real narx»): o'z bazamizdagi o'xshash mashinalar medianasi ------------------
const INSIGHT_SQL = `
  select percentile_cont(0.5) within group (order by price_usd) as median, count(*)::int as n
  from cars
  where lower(model) = lower($1) and year between $2 - 1 and $2 + 1
    and price_usd is not null and id <> $3
    and status in ('active','reserved','sold')
    and coalesce(sold_at, published_at, created_at) > now() - interval '365 days'`;

const MIN_COMPARABLES = 3;

export function priceInsight(price, median, n) {
  if (!price || !median || n < MIN_COMPARABLES) return null;
  const diffPct = Math.round(((median - price) / median) * 100);
  // Faqat haqiqatan foydali bo'lsa ko'rsatamiz: arzon yoki bozor narxida
  if (diffPct >= 5) return { kind: "cheaper", pct: diffPct, median: Math.round(median), comparables: n };
  if (diffPct > -5) return { kind: "fair", pct: diffPct, median: Math.round(median), comparables: n };
  return null;
}

async function insightFor(car) {
  if (!car.model || !car.year || !car.price_usd) return null;
  const r = await pool.query(INSIGHT_SQL, [car.model, car.year, car.id]);
  const median = r.rows[0]?.median == null ? null : Number(r.rows[0].median);
  return priceInsight(Number(car.price_usd), median, r.rows[0]?.n || 0);
}

const PUBLIC_COLUMNS = `id, status, brand, model, year, mileage_km, price_usd, color, transmission, fuel,
  position, paint_status, has_accident, location, notes, published_at,
  coalesce(array_length(photo_file_ids, 1), 0) as photos_count`;

function publicCar(row) {
  return {
    id: row.id,
    reserved: row.status === "reserved",
    brand: row.brand,
    model: row.model,
    year: row.year,
    mileage_km: row.mileage_km,
    price_usd: row.price_usd == null ? null : Number(row.price_usd),
    color: row.color,
    transmission: row.transmission,
    fuel: row.fuel,
    position: row.position,
    paint_status: row.paint_status,
    has_accident: row.has_accident,
    location: row.location,
    notes: row.notes,
    photos_count: row.photos_count,
    days_on_sale: row.published_at
      ? Math.max(0, Math.floor((Date.now() - new Date(row.published_at).getTime()) / 86400000))
      : null,
  };
}

function intOrNull(v) {
  const n = Number.parseInt(String(v ?? ""), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

publicRouter.get("/meta", asyncHandler(async (_req, res) => {
  let brands = [];
  try {
    const r = await pool.query(
      `select brand, model, count(*)::int as n from cars
       where status = any($1) and brand is not null group by brand, model order by brand, n desc`,
      [OFFERABLE],
    );
    const map = new Map();
    for (const row of r.rows) {
      if (!map.has(row.brand)) map.set(row.brand, { brand: row.brand, count: 0, models: [] });
      const b = map.get(row.brand);
      b.count += row.n;
      if (row.model) b.models.push({ model: row.model, count: row.n });
    }
    brands = [...map.values()].sort((a, b) => b.count - a.count);
  } catch {
    brands = [];
  }
  res.set("Cache-Control", "public, max-age=60");
  res.json({
    business: {
      name: process.env.BUSINESS_NAME || "Real Avto",
      address: process.env.BUSINESS_ADDRESS || "Yangiyo'l",
      hours: process.env.BUSINESS_HOURS || "",
      map_url: process.env.REAL_AVTO_MAP_URL || "",
      phones: (process.env.SALES_PHONE || "").split(/[,;|]/).map((p) => p.trim()).filter(Boolean),
    },
    bot_username: (process.env.BOT_USERNAME || "").replace(/^@/, ""),
    usd_rate_uzs: Number.parseInt(process.env.USD_RATE_UZS || "0", 10) || null,
    brands,
  });
}));

publicRouter.get("/cars", asyncHandler(async (req, res) => {
  const args = [OFFERABLE];
  let where = "where status = any($1)";
  const q = String(req.query.q || "").trim().slice(0, 60);
  if (q) {
    where += ` and (coalesce(brand,'') || ' ' || coalesce(model,'') || ' ' || coalesce(year::text,'')) ilike $${args.push(`%${q}%`)}`;
  }
  const brand = String(req.query.brand || "").trim();
  if (brand) where += ` and lower(brand) = lower($${args.push(brand)})`;
  const model = String(req.query.model || "").trim();
  if (model) where += ` and lower(model) = lower($${args.push(model)})`;
  const yearMin = intOrNull(req.query.year_min);
  if (yearMin) where += ` and year >= $${args.push(yearMin)}`;
  const priceMax = intOrNull(req.query.price_max);
  if (priceMax) where += ` and price_usd <= $${args.push(priceMax)}`;
  const transmission = String(req.query.transmission || "");
  if (["avtomat", "mexanika"].includes(transmission)) where += ` and transmission = $${args.push(transmission)}`;
  const order = {
    cheap: "price_usd asc nulls last",
    expensive: "price_usd desc nulls last",
    new: "published_at desc nulls last",
    year: "year desc nulls last",
  }[String(req.query.sort || "new")] || "published_at desc nulls last";
  const page = Math.max(1, intOrNull(req.query.page) || 1);
  try {
    const total = (await pool.query(`select count(*)::int as n from cars ${where}`, args)).rows[0].n;
    args.push(PAGE_SIZE, (page - 1) * PAGE_SIZE);
    const r = await pool.query(
      `select ${PUBLIC_COLUMNS} from cars ${where} order by status asc, ${order}, id desc
       limit $${args.length - 1} offset $${args.length}`,
      args,
    );
    const items = await Promise.all(r.rows.map(async (row) => ({ ...publicCar(row), insight: await insightFor(row) })));
    res.set("Cache-Control", "public, max-age=30");
    res.json({ items, total, page, page_size: PAGE_SIZE });
  } catch {
    res.json({ items: [], total: 0, page, page_size: PAGE_SIZE });
  }
}));

publicRouter.get("/cars/:id", asyncHandler(async (req, res) => {
  const id = intOrNull(req.params.id);
  if (!id) return res.status(400).json({ error: "invalid_id" });
  const r = await pool.query(`select ${PUBLIC_COLUMNS} from cars where id = $1 and status = any($2)`, [id, OFFERABLE]);
  const row = r.rows[0];
  if (!row) return res.status(404).json({ error: "not_found" });
  const similar = await pool.query(
    `select ${PUBLIC_COLUMNS} from cars
     where status = any($1) and id <> $2
       and (lower(model) = lower($3) or price_usd between $4 * 0.8 and $4 * 1.15)
     order by (lower(model) = lower($3)) desc, abs(coalesce(price_usd, 0) - $4) asc
     limit 6`,
    [OFFERABLE, id, row.model || "", Number(row.price_usd || 0)],
  );
  res.set("Cache-Control", "public, max-age=30");
  res.json({ car: { ...publicCar(row), insight: await insightFor(row) }, similar: similar.rows.map(publicCar) });
}));

// --- Rasm proksi: Telegram file_id → rasm (bot tokeni brauzerga hech qachon chiqmaydi) -----------
const photoCache = new Map(); // "carId:idx" → {buf, type}
const PHOTO_CACHE_MAX = 300;

async function fetchTelegramFile(fileId) {
  const meta = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/getFile?file_id=${encodeURIComponent(fileId)}`);
  const json = await meta.json();
  if (!json.ok) throw new Error("getFile failed");
  const file = await fetch(`https://api.telegram.org/file/bot${BOT_TOKEN}/${json.result.file_path}`);
  if (!file.ok) throw new Error("download failed");
  return { buf: Buffer.from(await file.arrayBuffer()), type: file.headers.get("content-type") || "image/jpeg" };
}

publicRouter.get("/photo/:carId/:idx", asyncHandler(async (req, res) => {
  const carId = intOrNull(req.params.carId);
  const idx = Number.parseInt(String(req.params.idx), 10);
  if (!carId || !Number.isFinite(idx) || idx < 0 || idx > 20) return res.status(400).end();
  if (!BOT_TOKEN) return res.status(503).json({ error: "photos_disabled" });
  const key = `${carId}:${idx}`;
  let hit = photoCache.get(key);
  if (!hit) {
    const r = await pool.query("select photo_file_ids from cars where id = $1 and status = any($2)", [carId, OFFERABLE]);
    const fileId = r.rows[0]?.photo_file_ids?.[idx];
    if (!fileId) return res.status(404).end();
    try {
      hit = await fetchTelegramFile(fileId);
    } catch {
      return res.status(502).end();
    }
    if (photoCache.size >= PHOTO_CACHE_MAX) photoCache.delete(photoCache.keys().next().value);
    photoCache.set(key, hit);
  }
  res.set("Content-Type", hit.type);
  res.set("Cache-Control", "public, max-age=86400");
  res.send(hit.buf);
}));

// --- Telegram Mini App: «Chiqsa xabar ber» (initData imzosi tekshiriladi) ------------------------
export function verifyInitData(initData, botToken, maxAgeSeconds = 86400) {
  if (!initData || !botToken) return null;
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return null;
  params.delete("hash");
  const dataCheck = [...params.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
  const secret = crypto.createHmac("sha256", "WebAppData").update(botToken).digest();
  const expected = crypto.createHmac("sha256", secret).update(dataCheck).digest("hex");
  const a = Buffer.from(expected, "hex");
  const b = Buffer.from(hash, "hex");
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  const authDate = Number.parseInt(params.get("auth_date") || "0", 10);
  if (!authDate || Date.now() / 1000 - authDate > maxAgeSeconds) return null;
  try {
    return JSON.parse(params.get("user") || "null");
  } catch {
    return null;
  }
}

publicRouter.post("/alerts", asyncHandler(async (req, res) => {
  const user = verifyInitData(String(req.headers["x-telegram-init-data"] || ""), BOT_TOKEN);
  if (!user?.id) return res.status(401).json({ error: "telegram_auth_required" });
  const body = req.body || {};
  const brand = String(body.brand || "").trim().slice(0, 100);
  if (!brand) return res.status(400).json({ error: "brand_required" });
  const model = String(body.model || "").trim().slice(0, 100) || null;
  const yearMin = intOrNull(body.year_min) || 1990;
  const yearMax = intOrNull(body.year_max) || new Date().getFullYear() + 1;
  const budgetMax = intOrNull(body.budget_max_usd) || 1_000_000;
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(" ") || null;
  const client = await pool.connect();
  try {
    await client.query("begin");
    const c = await client.query(
      `insert into clients (telegram_id, full_name, source, status) values ($1, $2, 'TELEGRAM', 'NEW')
       on conflict (telegram_id) do update set full_name = coalesce(clients.full_name, excluded.full_name)
       returning id`,
      [user.id, fullName],
    );
    const clientId = c.rows[0].id;
    const active = await client.query("select count(*)::int as n from wishlist where client_id = $1 and is_active", [clientId]);
    if (active.rows[0].n >= 5) {
      await client.query("rollback");
      return res.status(409).json({ error: "too_many_alerts" });
    }
    const w = await client.query(
      `insert into wishlist (client_id, brand, model, year_min, year_max, budget_min, budget_max, condition_key, is_active)
       values ($1, $2, $3, $4, $5, null, $6, null, true) returning id`,
      [clientId, brand, model, yearMin, yearMax, budgetMax],
    );
    await client.query("commit");
    res.status(201).json({ ok: true, alert_id: w.rows[0].id });
  } catch (e) {
    await client.query("rollback").catch(() => {});
    throw e;
  } finally {
    client.release();
  }
}));
