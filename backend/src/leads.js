import express from "express";
import { pool } from "./db.js";
import { requireRole } from "./middleware.js";
import { asyncHandler, getPagination } from "./utils.js";

/** AI savdo agenti leadlari (bot yaratadigan `leads` / `agent_messages` jadvallari). */
export const leadsRouter = express.Router();

const STATUSES = ["active", "handed_off", "in_progress", "won", "lost"];
const OPEN = ["active", "handed_off", "in_progress"];

const LIST_COLUMNS = `l.id, l.telegram_id, l.name, l.username, l.phone, l.status, l.score, l.channel,
  l.budget_usd, l.payment_method, l.visit_time, l.wants, l.summary, l.handoff_reason, l.human_mode,
  l.assigned_admin_id, l.handed_off_at, l.closed_at, l.last_message_at, l.created_at, l.car_id,
  c.brand as car_brand, c.model as car_model, c.year as car_year, c.price_usd as car_price_usd,
  (select count(*) from agent_messages m where m.lead_id = l.id)::int as messages_count`;

leadsRouter.get("/", asyncHandler(async (req, res) => {
  const status = String(req.query.status || "").trim().toLowerCase();
  const q = String(req.query.q || "").trim();
  const { limit, offset, page } = getPagination(req, { defaultLimit: 50, maxLimit: 200 });
  const args = [];
  let where = "where 1=1";
  if (status === "open") {
    where += ` and l.status = any($${args.push(OPEN)})`;
  } else if (STATUSES.includes(status)) {
    where += ` and l.status = $${args.push(status)}`;
  }
  if (q) {
    where += ` and (coalesce(l.name,'') || ' ' || coalesce(l.username,'') || ' ' || coalesce(l.phone,'') || ' ' || coalesce(l.wants,'')) ilike $${args.push(`%${q}%`)}`;
  }
  try {
    const countRes = await pool.query(`select count(*) from leads l ${where}`, args);
    const total = Number.parseInt(countRes.rows[0].count, 10);
    args.push(limit, offset);
    const r = await pool.query(
      `select ${LIST_COLUMNS} from leads l left join cars c on c.id = l.car_id ${where}
       order by (l.status = 'handed_off') desc, l.score desc, coalesce(l.last_message_at, l.created_at) desc
       limit $${args.length - 1} offset $${args.length}`,
      args,
    );
    res.json({ items: r.rows, total, page, limit });
  } catch {
    res.json({ items: [], total: 0, page, limit });
  }
}));

leadsRouter.get("/stats", asyncHandler(async (req, res) => {
  const days = Math.min(365, Math.max(1, Number.parseInt(String(req.query.days || "30"), 10) || 30));
  const since = "now() - ($1 || ' days')::interval";
  try {
    const [byStatus, created, won, handoff, waiting] = await Promise.all([
      pool.query("select status, count(*)::int as n from leads group by status"),
      pool.query(`select count(*)::int as n from leads where created_at >= ${since}`, [String(days)]),
      pool.query(`select count(*)::int as n from leads where status='won' and closed_at >= ${since}`, [String(days)]),
      pool.query(
        `select count(*)::int as n,
                avg(extract(epoch from (handed_off_at - created_at)) / 60) as avg_minutes
         from leads where handed_off_at is not null and created_at >= ${since}`,
        [String(days)],
      ),
      pool.query("select count(*)::int as n from leads where status='handed_off' and assigned_admin_id is null"),
    ]);
    const createdN = created.rows[0].n;
    res.json({
      days,
      byStatus: Object.fromEntries(byStatus.rows.map((r) => [r.status, r.n])),
      created: createdN,
      handedOff: handoff.rows[0].n,
      won: won.rows[0].n,
      conversionPct: createdN ? Math.round((won.rows[0].n / createdN) * 1000) / 10 : null,
      avgMinutesToHandoff: handoff.rows[0].avg_minutes == null ? null : Math.round(Number(handoff.rows[0].avg_minutes)),
      waitingForManager: waiting.rows[0].n,
    });
  } catch {
    res.json({ days, byStatus: {}, created: 0, handedOff: 0, won: 0, conversionPct: null, avgMinutesToHandoff: null, waitingForManager: 0 });
  }
}));

leadsRouter.get("/:id", asyncHandler(async (req, res) => {
  const id = Number.parseInt(String(req.params.id), 10);
  if (!Number.isFinite(id) || id < 1) return res.status(400).json({ error: "invalid_id" });
  try {
    const l = await pool.query(`select ${LIST_COLUMNS} from leads l left join cars c on c.id = l.car_id where l.id=$1`, [id]);
    if (!l.rows[0]) return res.status(404).json({ error: "not_found" });
    const m = await pool.query(
      "select id, role, content, created_at from agent_messages where lead_id=$1 order by id asc limit 500",
      [id],
    );
    res.json({ lead: l.rows[0], messages: m.rows });
  } catch {
    res.status(404).json({ error: "not_found" });
  }
}));

leadsRouter.patch("/:id", requireRole("admin", "manager"), asyncHandler(async (req, res) => {
  const id = Number.parseInt(String(req.params.id), 10);
  if (!Number.isFinite(id) || id < 1) return res.status(400).json({ error: "invalid_id" });
  const status = String((req.body || {}).status || "");
  if (!["won", "lost"].includes(status)) return res.status(400).json({ error: "invalid_status" });
  const r = await pool.query(
    `update leads set status=$1, human_mode=false, closed_at=now(), updated_at=now()
     where id=$2 and status = any($3) returning id, status`,
    [status, id, OPEN],
  );
  if (!r.rows[0]) return res.status(409).json({ error: "not_open" });
  res.json(r.rows[0]);
}));
