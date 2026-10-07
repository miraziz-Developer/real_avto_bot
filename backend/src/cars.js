import express from "express";
import { pool } from "./db.js";
import { requireRole } from "./middleware.js";
import { asyncHandler, getPagination, parseId } from "./utils.js";

/** Mashinalar bazasi (bot yaratadigan `cars` / `car_events` jadvallari). */
export const carsRouter = express.Router();

const STATUSES = ["review", "active", "reserved", "sold", "archived"];
const STALE_DAYS = Math.max(1, Number.parseInt(process.env.CAR_STALE_DAYS || "7", 10) || 7);

const LIST_COLUMNS = `id, status, source, brand, model, year, mileage_km, price_usd, color, transmission, fuel,
  position, paint_status, has_accident, location, is_own, purchase_price_usd, expenses_usd, sold_price_usd,
  ai_confidence, channel_chat_id, channel_message_ids, published_at, sold_at, created_at, updated_at,
  coalesce(array_length(photo_file_ids, 1), 0) as photos_count`;

carsRouter.get("/", asyncHandler(async (req, res) => {
  const status = String(req.query.status || "").trim().toLowerCase();
  const q = String(req.query.q || "").trim();
  const { limit, offset, page } = getPagination(req, { defaultLimit: 50, maxLimit: 200 });
  const args = [];
  let where = "where 1=1";
  if (STATUSES.includes(status)) {
    args.push(status);
    where += ` and status = $${args.length}`;
  }
  if (q) {
    args.push(`%${q}%`);
    where += ` and (coalesce(brand,'') || ' ' || coalesce(model,'') || ' ' || coalesce(year::text,'')) ilike $${args.length}`;
  }
  try {
    const countRes = await pool.query(`select count(*) from cars ${where}`, args);
    const total = Number.parseInt(countRes.rows[0].count, 10);
    args.push(limit, offset);
    const r = await pool.query(
      `select ${LIST_COLUMNS} from cars ${where}
       order by coalesce(published_at, created_at) desc, id desc
       limit $${args.length - 1} offset $${args.length}`,
      args,
    );
    res.json({ items: r.rows, total, page, limit });
  } catch {
    res.json({ items: [], total: 0, page, limit });
  }
}));

carsRouter.get("/stats", asyncHandler(async (req, res) => {
  const days = Math.min(365, Math.max(1, Number.parseInt(String(req.query.days || "30"), 10) || 30));
  try {
    const [byStatus, activeValue, sold, stale, weekly, newCount] = await Promise.all([
      pool.query("select status, count(*)::int as n from cars group by status"),
      pool.query("select coalesce(sum(price_usd),0)::bigint as v from cars where status='active'"),
      pool.query(
        `select brand, model, price_usd, sold_price_usd, purchase_price_usd, expenses_usd, is_own,
                extract(epoch from (sold_at - published_at)) / 86400 as days_to_sell
         from cars where status='sold' and sold_at >= now() - ($1 || ' days')::interval`,
        [String(days)],
      ),
      pool.query(
        `select count(*)::int as n from cars
         where status='active' and published_at < now() - ($1 || ' days')::interval`,
        [String(STALE_DAYS)],
      ),
      pool.query(
        `select date_trunc('week', sold_at) as week, count(*)::int as n
         from cars where status='sold' and sold_at >= now() - interval '8 weeks'
         group by 1 order by 1`,
      ),
      pool.query(
        `select count(*)::int as n from cars
         where source <> 'import' and created_at >= now() - ($1 || ' days')::interval`,
        [String(days)],
      ),
    ]);
    const soldRows = sold.rows;
    const durations = soldRows.map((r) => Number(r.days_to_sell)).filter((d) => Number.isFinite(d) && d >= 0);
    const top = {};
    for (const r of soldRows) {
      const k = [r.brand, r.model].filter(Boolean).join(" ") || "Noma'lum";
      top[k] = (top[k] || 0) + 1;
    }
    const ownProfitUsd = soldRows
      .filter((r) => r.is_own && r.purchase_price_usd)
      .reduce(
        (s, r) => s + Number(r.sold_price_usd ?? r.price_usd ?? 0) - Number(r.purchase_price_usd) - Number(r.expenses_usd ?? 0),
        0,
      );
    res.json({
      days,
      byStatus: Object.fromEntries(byStatus.rows.map((r) => [r.status, r.n])),
      activeValueUsd: Number(activeValue.rows[0].v),
      soldCount: soldRows.length,
      newCount: newCount.rows[0].n,
      avgDaysToSell: durations.length ? Math.round((durations.reduce((a, b) => a + b, 0) / durations.length) * 10) / 10 : null,
      topSold: Object.entries(top).sort((a, b) => b[1] - a[1]).slice(0, 5),
      ownProfitUsd,
      staleCount: stale.rows[0].n,
      staleDays: STALE_DAYS,
      weekly: weekly.rows.map((r) => ({ week: r.week, n: r.n })),
    });
  } catch {
    res.json({ days, byStatus: {}, activeValueUsd: 0, soldCount: 0, newCount: 0, avgDaysToSell: null, topSold: [], ownProfitUsd: 0, staleCount: 0, staleDays: STALE_DAYS, weekly: [] });
  }
}));

carsRouter.get("/:id", asyncHandler(async (req, res) => {
  const id = parseId(req.params.id);
  if (!id) return res.status(400).json({ error: "invalid_id" });
  try {
    const c = await pool.query(`select ${LIST_COLUMNS}, notes, raw_text, listing_submission_id from cars where id=$1`, [id]);
    if (!c.rows[0]) return res.status(404).json({ error: "not_found" });
    const ev = await pool.query(
      "select id, kind, data, actor_telegram_id, created_at from car_events where car_id=$1 order by created_at desc limit 100",
      [id],
    );
    res.json({ car: c.rows[0], events: ev.rows });
  } catch {
    res.status(404).json({ error: "not_found" });
  }
}));

const MONEY_FIELDS = ["price_usd", "purchase_price_usd", "expenses_usd", "sold_price_usd"];

function toMoney(v) {
  if (v === null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 && n <= 5_000_000 ? Math.round(n) : undefined;
}

carsRouter.patch("/:id", requireRole("admin", "manager"), asyncHandler(async (req, res) => {
  const id = parseId(req.params.id);
  if (!id) return res.status(400).json({ error: "invalid_id" });
  const body = req.body || {};
  const client = await pool.connect();
  try {
    await client.query("begin");
    const cur = await client.query("select * from cars where id=$1 for update", [id]);
    const car = cur.rows[0];
    if (!car) {
      await client.query("rollback");
      return res.status(404).json({ error: "not_found" });
    }
    const sets = [];
    const args = [];
    const changes = {};
    const set = (col, val) => {
      args.push(val);
      sets.push(`${col}=$${args.length}`);
    };

    for (const f of MONEY_FIELDS) {
      if (!(f in body)) continue;
      const v = toMoney(body[f]);
      if (v === undefined) {
        await client.query("rollback");
        return res.status(400).json({ error: `invalid_${f}` });
      }
      if (Number(car[f] ?? -1) !== Number(v ?? -1)) {
        set(f, v);
        changes[f] = { old: car[f], new: v };
      }
    }
    if ("purchase_price_usd" in body && body.purchase_price_usd != null && !car.is_own) {
      set("is_own", true);
    }
    if ("notes" in body && typeof body.notes === "string" && body.notes !== car.notes) {
      set("notes", body.notes.slice(0, 2000));
      changes.notes = { old: car.notes, new: body.notes.slice(0, 2000) };
    }
    let statusChange = null;
    if ("status" in body) {
      const st = String(body.status);
      if (!STATUSES.includes(st)) {
        await client.query("rollback");
        return res.status(400).json({ error: "invalid_status" });
      }
      if (st !== car.status) {
        set("status", st);
        if (st === "sold") sets.push("sold_at=now()");
        else if (car.status === "sold") sets.push("sold_at=null");
        if (st === "active") sets.push("stale_prompted_at=null", "published_at=coalesce(published_at, now())");
        statusChange = { old: car.status, new: st };
      }
    }
    if (!sets.length) {
      await client.query("rollback");
      return res.json({ car, changed: false });
    }
    sets.push("updated_at=now()");
    args.push(id);
    const upd = await client.query(`update cars set ${sets.join(", ")} where id=$${args.length} returning *`, args);
    const actor = `crm:${req.user?.username || "?"}`;
    if (changes.price_usd) {
      await client.query("insert into car_events(car_id, kind, data) values($1,'price_changed',$2)", [
        id,
        { ...changes.price_usd, actor },
      ]);
    }
    const edited = Object.fromEntries(Object.entries(changes).filter(([k]) => k !== "price_usd"));
    if (Object.keys(edited).length) {
      await client.query("insert into car_events(car_id, kind, data) values($1,'edited',$2)", [id, { ...edited, actor }]);
    }
    if (statusChange) {
      await client.query("insert into car_events(car_id, kind, data) values($1,'status_changed',$2)", [
        id,
        { ...statusChange, actor },
      ]);
    }
    await client.query("commit");
    res.json({ car: upd.rows[0], changed: true });
  } catch (e) {
    await client.query("rollback").catch(() => {});
    throw e;
  } finally {
    client.release();
  }
}));
