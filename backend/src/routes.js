import express from "express";
import { pool } from "./db.js";
import { signAccessToken } from "./auth.js";
import { requireAuth, requireRole } from "./middleware.js";
import { asyncHandler, getPagination, sha256 } from "./utils.js";
import { carsRouter } from "./cars.js";

export const router = express.Router();

/** Jadval bot tomonidan keyinroq yaratilishi mumkin — xato bo‘lsa 0. */
async function scalarCount(sql, params = []) {
  try {
    const r = await pool.query(sql, params);
    return Number(r.rows[0]?.count ?? 0);
  } catch {
    return 0;
  }
}

router.get('/health', (_req, res) => res.json({ ok: true }));

router.get('/login', (req, res) => {
  const host = req.headers.host?.replace(/:\d+$/, '') || '167.172.80.246';
  res.redirect(`http://${host}:3000/login`);
});

router.get('/', (_req, res) => {
  res.redirect('/login');
});

router.post('/auth/login', asyncHandler(async (req, res) => {

  const { username, password } = req.body || {};
  const r = await pool.query(
    "select id, username, role, password_hash, is_active from crm_users where username=$1 limit 1",
    [username],
  );
  const u = r.rows[0];
  if (!u || !u.is_active || u.password_hash !== sha256(password || "")) {
    return res.status(401).json({ error: 'invalid_credentials' });
  }
  return res.json({ token: signAccessToken({ id: u.id, username: u.username, role: u.role }), user: { id: u.id, username: u.username, role: u.role } });
}));

router.use(requireAuth);

router.use('/cars', carsRouter);

router.get('/stats', asyncHandler(async (_req, res) => {
  const [
    clients,
    listingsPending,
    listingsApproved,
    listingsRejected,
    wishlistsActive,
    listingThreads,
    contestParticipants,
    usersTg,
  ] = await Promise.all([
    pool.query('select count(*)::int as count from clients'),
    scalarCount("select count(*)::int as count from listing_submissions where lower(status::text)='pending'"),
    scalarCount("select count(*)::int as count from listing_submissions where lower(status::text)='approved'"),
    scalarCount("select count(*)::int as count from listing_submissions where lower(status::text)='rejected'"),
    scalarCount('select count(*)::int as count from wishlist where is_active = true'),
    scalarCount('select count(*)::int as count from listing_threads'),
    scalarCount('select count(*)::int as count from contest_participants'),
    scalarCount('select count(*)::int as count from users'),
  ]);
  res.json({
    clients: clients.rows[0].count,
    listingsPending,
    listingsApproved,
    listingsRejected,
    wishlistsActive,
    listingThreads,
    contestParticipants,
    usersTg,
  });
}));

router.get('/clients', asyncHandler(async (req, res) => {
  const q = String(req.query.q || '').trim();
  const status = String(req.query.status || '').trim();
  const { limit, offset, page } = getPagination(req, { defaultLimit: 50, maxLimit: 200 });
  const args = [];
  let where = 'where 1=1';
  if (q) { args.push(`%${q}%`); where += ` and (coalesce(full_name,'') ilike $${args.length} or coalesce(phone,'') ilike $${args.length})`; }
  if (status) { args.push(status); where += ` and status = $${args.length}`; }
  const countArgs = [...args];
  const countRes = await pool.query(`select count(*) from clients ${where}`, countArgs);
  const total = Number.parseInt(countRes.rows[0].count, 10);
  args.push(limit, offset);
  const r = await pool.query(
    `select * from clients ${where} order by created_at desc limit $${args.length - 1} offset $${args.length}`,
    args,
  );
  res.json({ items: r.rows, total, page, limit });
}));

router.patch('/clients/:id', requireRole('admin', 'manager'), asyncHandler(async (req, res) => {
  const { id } = req.params;
  const { status, notes } = req.body || {};
  const r = await pool.query(
    'update clients set status=coalesce($1,status), notes=coalesce($2,notes), updated_at=now() where id=$3 returning *',
    [status ?? null, notes ?? null, id],
  );
  if (!r.rows[0]) return res.status(404).json({ error: 'not_found' });
  res.json(r.rows[0]);
}));

router.get('/clients/:id/detail', asyncHandler(async (req, res) => {
  const id = Number.parseInt(String(req.params.id), 10);
  if (!Number.isFinite(id) || id < 1) return res.status(400).json({ error: 'invalid_id' });
  const c = await pool.query('select * from clients where id = $1', [id]);
  if (!c.rows[0]) return res.status(404).json({ error: 'not_found' });
  const client = c.rows[0];
  const [listings, wishlists, parts, tgUser] = await Promise.all([
    pool
      .query(
        'select * from listing_submissions where client_id = $1 order by created_at desc limit 100',
        [id],
      )
      .catch(() => ({ rows: [] })),
    pool.query('select * from wishlist where client_id = $1 order by id desc limit 80', [id]).catch(() => ({ rows: [] })),
    pool
      .query(
        `select cp.id, cp.contest_id, cp.client_id, cp.joined_at, cp.is_winner, ct.title as contest_title, ct.prize, ct.is_active as contest_active
         from contest_participants cp
         join contests ct on ct.id = cp.contest_id
         where cp.client_id = $1
         order by cp.joined_at desc`,
        [id],
      )
      .catch(() => ({ rows: [] })),
    pool
      .query(
        'select id, tg_id, username, first_name, referral_code, referrals_count, channel_ok, instagram_ok, leaderboard_alias from users where tg_id = $1 limit 1',
        [client.telegram_id],
      )
      .catch(() => ({ rows: [] })),
  ]);
  res.json({
    client,
    listings: listings.rows,
    wishlists: wishlists.rows,
    contest_participations: parts.rows,
    telegram_user: tgUser.rows[0] || null,
  });
}));

/** Botdagi «Saqlangan qidiruv» (wishlist) — barcha mijozlar. */
router.get('/wishlists', asyncHandler(async (req, res) => {
  const { limit, offset, page } = getPagination(req, { defaultLimit: 50, maxLimit: 200 });
  try {
    const countRes = await pool.query('select count(*) from wishlist');
    const total = Number.parseInt(countRes.rows[0].count, 10);
    const r = await pool.query(
      `select w.*,
             cl.id as client_db_id,
             cl.full_name as client_name,
             cl.phone as client_phone,
             cl.telegram_id as client_telegram_id
      from wishlist w
      left join clients cl on cl.id = w.client_id
      order by w.id desc
      limit $1 offset $2`,
      [limit, offset],
    );
    res.json({ items: r.rows, total, page, limit });
  } catch {
    res.json({ items: [], total: 0, page, limit });
  }
}));

router.get('/contest/active', asyncHandler(async (_req, res) => {
  const r = await pool.query('select * from contests where is_active=true order by id desc limit 1');
  res.json({ contest: r.rows[0] || null });
}));

router.get('/contest/history', asyncHandler(async (_req, res) => {
  const r = await pool.query(`
    select c.*,
           w.full_name as winner_name,
           w.telegram_id as winner_telegram_id
    from contests c
    left join clients w on w.id = c.winner_client_id
    order by c.id desc
    limit 100
  `);
  res.json(r.rows);
}));

router.post('/contest', requireRole('admin'), asyncHandler(async (req, res) => {
  const { title, prize, end_date } = req.body || {};
  if (!title || !prize || !end_date) return res.status(400).json({ error: 'missing_fields' });
  await pool.query('update contests set is_active=false where is_active=true');
  const r = await pool.query(
    `insert into contests(title, prize, start_date, end_date, is_active)
     values($1,$2,now(),$3,true) returning *`,
    [title, prize, end_date],
  );
  res.status(201).json(r.rows[0]);
}));

router.post('/contest/:id/pick-winner', requireRole('admin'), asyncHandler(async (req, res) => {
  const { id } = req.params;
  const participants = await pool.query('select * from contest_participants where contest_id=$1', [id]);
  if (!participants.rows.length) return res.status(400).json({ error: 'no_participants' });
  const winner = participants.rows[Math.floor(Math.random() * participants.rows.length)];
  await pool.query('update contest_participants set is_winner=(id=$1) where contest_id=$2', [winner.id, id]);
  const updated = await pool.query('update contests set winner_client_id=$1, is_active=false where id=$2 returning *', [winner.client_id, id]);
  res.json({ contest: updated.rows[0], winner });
}));

router.get('/listings', asyncHandler(async (req, res) => {
  const status = String(req.query.status || '').trim().toLowerCase();
  const { limit, offset, page } = getPagination(req, { defaultLimit: 50, maxLimit: 200 });
  const args = [];
  let where = 'where 1=1';
  if (status && ['pending', 'approved', 'rejected'].includes(status)) {
    args.push(status);
    where += ` and lower(ls.status::text) = $${args.length}`;
  }
  try {
    const countRes = await pool.query(`select count(*) from listing_submissions ls ${where}`, args);
    const total = Number.parseInt(countRes.rows[0].count, 10);
    args.push(limit, offset);
    const r = await pool.query(
      `select ls.*, cl.id as client_db_id, cl.full_name as client_name, cl.phone as client_phone, cl.telegram_id as client_telegram_id
       from listing_submissions ls
       left join clients cl on cl.id = ls.client_id
       ${where}
       order by ls.created_at desc
       limit $${args.length - 1} offset $${args.length}`,
      args,
    );
    res.json({ items: r.rows, total, page, limit });
  } catch {
    res.json({ items: [], total: 0, page, limit });
  }
}));

router.get('/listings/:id', asyncHandler(async (req, res) => {
  const id = Number.parseInt(String(req.params.id), 10);
  if (!Number.isFinite(id) || id < 1) return res.status(400).json({ error: 'invalid_id' });
  try {
    const r = await pool.query(
      `select ls.*, cl.full_name as client_name, cl.phone as client_phone, cl.telegram_id as client_telegram_id, cl.id as client_db_id
       from listing_submissions ls
       left join clients cl on cl.id = ls.client_id
       where ls.id = $1`,
      [id],
    );
    if (!r.rows[0]) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0]);
  } catch {
    return res.status(404).json({ error: 'not_found' });
  }
}));

router.get('/contest-participants', asyncHandler(async (req, res) => {
  const { limit, offset, page } = getPagination(req, { defaultLimit: 50, maxLimit: 200 });
  try {
    const countRes = await pool.query('select count(*) from contest_participants');
    const total = Number.parseInt(countRes.rows[0].count, 10);
    const r = await pool.query(
      `select cp.id, cp.contest_id, cp.client_id, cp.joined_at, cp.is_winner,
              ct.title as contest_title, ct.prize, ct.is_active as contest_active,
              cl.full_name, cl.phone, cl.telegram_id
       from contest_participants cp
       join contests ct on ct.id = cp.contest_id
       left join clients cl on cl.id = cp.client_id
       order by cp.joined_at desc
       limit $1 offset $2`,
      [limit, offset],
    );
    res.json({ items: r.rows, total, page, limit });
  } catch {
    res.json({ items: [], total: 0, page, limit });
  }
}));
