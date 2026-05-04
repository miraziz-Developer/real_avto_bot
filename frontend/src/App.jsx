import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";

const baseURL = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "") || "/api";
const publicApi = axios.create({ baseURL });

const TABS = [
  { id: "dashboard", label: "Boshqaruv", ic: "◆" },
  { id: "clients", label: "Mijozlar", ic: "◎" },
  { id: "listings", label: "E'lonlar (TG)", ic: "▣" },
  { id: "wishlists", label: "Qidiruvlar (TG)", ic: "◇" },
  { id: "contest", label: "Konkurs", ic: "★" },
];

function formatUsd(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `$${Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function formatDate(v) {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleString("uz-UZ", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return String(v);
  }
}

function statusBadge(status) {
  const s = String(status || "").toLowerCase();
  let cls = "b-muted";
  if (s === "pending") cls = "b-pending";
  if (s === "approved" || s === "active" || s === "interested") cls = "b-approved";
  if (s === "rejected" || s === "not_interested") cls = "b-rejected";
  if (s === "closed" || s === "false") cls = "b-muted";
  return <span className={`badge ${cls}`}>{s || "—"}</span>;
}

function includesAny(items, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  return items.some((x) => String(x || "").toLowerCase().includes(q));
}

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("crm_token") || "");
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("crm_user") || "null");
    } catch {
      return null;
    }
  });
  const [tab, setTab] = useState("dashboard");
  const [query, setQuery] = useState("");
  const [listingFilter, setListingFilter] = useState("");
  const [state, setState] = useState({
    stats: null,
    clients: [],
    wishlists: [],
    contests: [],
    listings: [],
    participants: [],
  });
  const [error, setError] = useState("");
  const [clientSheetId, setClientSheetId] = useState(null);
  const [clientDetail, setClientDetail] = useState(null);
  const [clientDetailLoading, setClientDetailLoading] = useState(false);

  const api = useMemo(() => {
    const a = axios.create({ baseURL });
    a.interceptors.request.use((cfg) => {
      if (token) cfg.headers.Authorization = `Bearer ${token}`;
      return cfg;
    });
    return a;
  }, [token]);

  const canEdit = user?.role === "admin" || user?.role === "manager";

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [stats, clients, wishlists, contests, listings, participants] = await Promise.all([
        api.get("/stats"),
        api.get("/clients"),
        api.get("/wishlists").catch(() => ({ data: [] })),
        api.get("/contest/history"),
        api.get("/listings").catch(() => ({ data: [] })),
        api.get("/contest-participants").catch(() => ({ data: [] })),
      ]);
      setState({
        stats: stats.data,
        clients: clients.data,
        wishlists: wishlists.data || [],
        contests: contests.data,
        listings: listings.data || [],
        participants: participants.data || [],
      });
      setError("");
    } catch (e) {
      const status = e?.response?.status;
      if (status === 401) {
        localStorage.removeItem("crm_token");
        localStorage.removeItem("crm_user");
        setToken("");
        setUser(null);
        setError("Sessiya tugadi — qayta kiring.");
        return;
      }
      setError(e?.message || "Server xatosi.");
    }
  }, [token, api]);

  useEffect(() => {
    load();
    if (!token) return;
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [token, load]);

  useEffect(() => {
    if (!token || !clientSheetId) {
      setClientDetail(null);
      return;
    }
    let cancel = false;
    setClientDetailLoading(true);
    api
      .get(`/clients/${clientSheetId}/detail`)
      .then((r) => {
        if (!cancel) setClientDetail(r.data);
      })
      .catch(() => {
        if (!cancel) setClientDetail(null);
      })
      .finally(() => {
        if (!cancel) setClientDetailLoading(false);
      });
    return () => {
      cancel = true;
    };
  }, [token, clientSheetId, api]);

  async function login(e) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    try {
      const r = await publicApi.post("/auth/login", {
        username: String(fd.get("username") || ""),
        password: String(fd.get("password") || ""),
      });
      setToken(r.data.token);
      setUser(r.data.user);
      localStorage.setItem("crm_token", r.data.token);
      localStorage.setItem("crm_user", JSON.stringify(r.data.user));
      setError("");
    } catch {
      setError("Login xato. Username / parol.");
    }
  }

  async function updateClient(id, patch) {
    if (!canEdit) return;
    await api.patch(`/clients/${id}`, patch);
    await load();
    if (clientSheetId === id) {
      const r = await api.get(`/clients/${id}/detail`);
      setClientDetail(r.data);
    }
  }

  async function createContest(e) {
    e.preventDefault();
    if (user?.role !== "admin") return;
    const fd = new FormData(e.currentTarget);
    await api.post("/contest", {
      title: String(fd.get("title") || ""),
      prize: String(fd.get("prize") || ""),
      end_date: String(fd.get("end_date") || ""),
    });
    e.currentTarget.reset();
    await load();
  }

  async function pickContestWinner(contestId) {
    if (user?.role !== "admin") return;
    if (!window.confirm(`Konkurs #${contestId} uchun ishtirokchilar orasidan tasodifiy g‘olib tanlansinmi?`)) return;
    try {
      await api.post(`/contest/${contestId}/pick-winner`);
      setError("");
      await load();
    } catch (e) {
      const err = e?.response?.data?.error || e?.message || "Xato";
      setError(String(err));
    }
  }

  const filtered = useMemo(() => {
    const q = query;
    return {
      clients: state.clients.filter((x) => includesAny([x.full_name, x.phone, x.source, x.status], q)),
      wishlists: state.wishlists.filter((x) =>
        includesAny(
          [x.client_name, x.brand, x.model, x.client_phone, String(x.client_telegram_id || "")],
          q,
        ),
      ),
      listings: state.listings.filter((x) => {
        if (listingFilter && String(x.status || "").toLowerCase() !== listingFilter) return false;
        return includesAny(
          [x.brand, x.model, x.status, x.client_name, x.client_phone, String(x.user_telegram_id)],
          q,
        );
      }),
      participants: state.participants.filter((x) =>
        includesAny([x.full_name, x.contest_title, x.phone, String(x.telegram_id)], q),
      ),
    };
  }, [state, query, listingFilter]);

  if (!token) {
    return (
      <div className="authPage">
        <div className="authCard">
          <h1>Real Avto CRM</h1>
          <p className="sub">Boshqaruv paneli — barcha ma’lumotlar bitta joyda.</p>
          <form className="authForm" onSubmit={login}>
            <input name="username" placeholder="Login" autoComplete="username" required />
            <input name="password" type="password" placeholder="Parol" autoComplete="current-password" required />
            <button type="submit">Kirish</button>
          </form>
          {error && <p className="error">{error}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">RA</div>
          <div>
            <strong>Real Avto</strong>
            <span>CRM · Premium</span>
          </div>
        </div>
        <nav>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`navBtn ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              <span className="ic">{t.ic}</span>
              {t.label}
            </button>
          ))}
        </nav>
      </aside>

      <main className="content">
        <header className="topbar">
          <div>
            <h2>{TABS.find((t) => t.id === tab)?.label}</h2>
            <p className="role">
              Rol: <b>{user?.role}</b> · {user?.username}
            </p>
          </div>
          <div className="topActions">
            <input
              className="searchInput"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Qidiruv: ism, telefon, marka, status…"
            />
            <button type="button" className="ghost" onClick={load}>
              Yangilash
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                localStorage.removeItem("crm_token");
                localStorage.removeItem("crm_user");
                setToken("");
                setUser(null);
              }}
            >
              Chiqish
            </button>
          </div>
        </header>

        {tab === "dashboard" && (
          <Dashboard stats={state.stats} wishlists={state.wishlists} listings={state.listings} />
        )}
        {tab === "clients" && (
          <ClientsTable rows={filtered.clients} onOpen={(id) => setClientSheetId(id)} />
        )}
        {tab === "listings" && (
          <ListingsPage
            rows={filtered.listings}
            filter={listingFilter}
            onFilter={setListingFilter}
            onOpenClient={(cid) => {
              setTab("clients");
              setClientSheetId(cid);
            }}
          />
        )}
        {tab === "wishlists" && (
          <WishlistsPage
            rows={filtered.wishlists}
            onOpenClient={(cid) => {
              setTab("clients");
              setClientSheetId(cid);
            }}
          />
        )}
        {tab === "contest" && (
          <ContestPage
            rows={state.contests}
            participants={filtered.participants}
            onCreate={createContest}
            onPickWinner={pickContestWinner}
            isAdmin={user?.role === "admin"}
          />
        )}

        {error && <p className="error">{error}</p>}
      </main>

      {clientSheetId !== null && (
        <ClientSheet
          id={clientSheetId}
          loading={clientDetailLoading}
          data={clientDetail}
          onClose={() => {
            setClientSheetId(null);
            setClientDetail(null);
          }}
          canEdit={canEdit}
          onUpdateClient={updateClient}
        />
      )}
    </div>
  );
}

function Dashboard({ stats, wishlists, listings }) {
  const cards = [
    { k: "Mijozlar", v: stats?.clients, c: "c0" },
    { k: "E'lon: moderatsiya", v: stats?.listingsPending, c: "c0" },
    { k: "E'lon: kanalda", v: stats?.listingsApproved, c: "c2" },
    { k: "E'lon: rad", v: stats?.listingsRejected, c: "c4" },
    { k: "Faol qidiruv (wishlist)", v: stats?.wishlistsActive, c: "c1" },
    { k: "Savol iplari (anonim chat)", v: stats?.listingThreads, c: "c3" },
    { k: "Konkurs ishtirok", v: stats?.contestParticipants, c: "c3" },
    { k: "Telegram foyd.", v: stats?.usersTg, c: "c0" },
  ];
  const recentWishlists = [...wishlists].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 8);
  const recentListings = [...listings].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 8);
  return (
    <div>
      <div className="gridStats">
        {cards.map((x) => (
          <div key={x.k} className={`statCard ${x.c}`}>
            <div className="label">{x.k}</div>
            <div className="val">{x.v ?? "—"}</div>
          </div>
        ))}
      </div>
      <div className="split2">
        <div className="panel">
          <div className="panelHead">
            <h3>So‘nggi qidiruvlar (bot)</h3>
          </div>
          <MiniTable
            headers={["ID", "Mijoz", "Mashina", "Byudjet", "Faol"]}
            rows={recentWishlists.map((r) => [
              r.id,
              r.client_name || "—",
              `${r.brand || ""} ${r.model || ""}`.trim() || "—",
              `${r.budget_min != null ? formatUsd(r.budget_min) : "—"} … ${formatUsd(r.budget_max)}`,
              r.is_active ? "ha" : "yo‘q",
            ])}
          />
        </div>
        <div className="panel">
          <div className="panelHead">
            <h3>So‘nggi e’lonlar (Telegram)</h3>
          </div>
          <MiniTable
            headers={["ID", "Mashina", "Narx", "Holat"]}
            rows={recentListings.map((r) => [
              r.id,
              `${r.brand || ""} ${r.model || ""}`.trim() || "—",
              formatUsd(r.price_ask_usd),
              statusBadge(r.status),
            ])}
          />
        </div>
      </div>
    </div>
  );
}

function MiniTable({ headers, rows }) {
  return (
    <div className="tableWrap">
      <table className="data">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={headers.length} style={{ color: "var(--muted)" }}>
                Ma’lumot yo‘q
              </td>
            </tr>
          ) : (
            rows.map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j}>{c}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function ClientsTable({ rows, onOpen }) {
  return (
    <div className="panel">
      <div className="panelHead">
        <h3>Mijozlar ({rows.length})</h3>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>Qatorni bosing — to‘liq profil, e’lonlar, qidiruvlar</span>
      </div>
      <div className="tableWrap">
        <table className="data">
          <thead>
            <tr>
              <th>ID</th>
              <th>Ism</th>
              <th>Telefon</th>
              <th>Telegram ID</th>
              <th>Manba</th>
              <th>Holat</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => onOpen(r.id)}>
                <td className="mono">{r.id}</td>
                <td>{r.full_name || "—"}</td>
                <td className="mono">{r.phone || "—"}</td>
                <td className="mono">{r.telegram_id ?? "—"}</td>
                <td>{statusBadge(r.source)}</td>
                <td>{statusBadge(r.status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ListingsPage({ rows, filter, onFilter, onOpenClient }) {
  const chips = [
    { id: "", label: "Hammasi" },
    { id: "pending", label: "Moderatsiya" },
    { id: "approved", label: "Kanalda" },
    { id: "rejected", label: "Rad" },
  ];
  return (
    <div className="panel">
      <div className="panelHead">
        <h3>Telegram e’lonlari ({rows.length})</h3>
        <div className="chips">
          {chips.map((c) => (
            <button key={c.id || "all"} type="button" className={`chip ${filter === c.id ? "on" : ""}`} onClick={() => onFilter(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
      </div>
      <div className="tableWrap">
        <table className="data">
          <thead>
            <tr>
              <th>ID</th>
              <th>Mijoz</th>
              <th>Mashina</th>
              <th>Yil</th>
              <th>Narx (USD)</th>
              <th>Holat</th>
              <th>Sana</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>
                  {r.client_name || "—"}
                  <div className="mono" style={{ opacity: 0.75, marginTop: 2 }}>
                    {r.client_phone || ""}
                  </div>
                </td>
                <td>
                  <strong>
                    {r.brand} {r.model}
                  </strong>
                  <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 2 }}>{r.mileage != null ? `${r.mileage} km` : ""}</div>
                </td>
                <td>{r.year}</td>
                <td className="mono">{formatUsd(r.price_ask_usd)}</td>
                <td>{statusBadge(r.status)}</td>
                <td className="mono" style={{ fontSize: 12 }}>
                  {formatDate(r.created_at)}
                </td>
                <td>
                  {r.client_db_id != null && (
                    <button type="button" className="ghost" style={{ padding: "6px 10px", fontSize: 12 }} onClick={() => onOpenClient(r.client_db_id)}>
                      Mijoz
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function WishlistsPage({ rows, onOpenClient }) {
  return (
    <div className="panel">
      <div className="panelHead">
        <h3>Saqlangan qidiruvlar — bot ({rows.length})</h3>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          Foydalanuvchi kanalga mos e’lon bo‘lganda xabar oladi; CRMdan mijoz profiliga o‘tish mumkin.
        </span>
      </div>
      <div className="tableWrap">
        <table className="data">
          <thead>
            <tr>
              <th>ID</th>
              <th>Mijoz</th>
              <th>Marka / model</th>
              <th>Yil</th>
              <th>Byudjet (USD)</th>
              <th>Holat</th>
              <th>Sana</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>
                  {r.client_name || "—"}
                  <div className="mono" style={{ opacity: 0.75, marginTop: 2, fontSize: 11 }}>
                    {r.client_phone || ""} {r.client_telegram_id != null ? `· tg ${r.client_telegram_id}` : ""}
                  </div>
                </td>
                <td>
                  <strong>{r.brand}</strong> {r.model || ""}
                </td>
                <td className="mono">
                  {r.year_min}–{r.year_max}
                </td>
                <td className="mono">
                  {r.budget_min != null ? formatUsd(r.budget_min) : "—"} … {formatUsd(r.budget_max)}
                </td>
                <td>{r.is_active ? <span className="badge b-approved">faol</span> : <span className="badge b-muted">yopilgan</span>}</td>
                <td className="mono" style={{ fontSize: 12 }}>
                  {formatDate(r.created_at)}
                </td>
                <td>
                  {r.client_db_id != null && (
                    <button type="button" className="ghost" style={{ padding: "6px 10px", fontSize: 12 }} onClick={() => onOpenClient(r.client_db_id)}>
                      Mijoz
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ContestPage({ rows, participants, onCreate, onPickWinner, isAdmin }) {
  const countByContest = useMemo(() => {
    const m = new Map();
    for (const p of participants || []) {
      const id = p.contest_id;
      m.set(id, (m.get(id) || 0) + 1);
    }
    return m;
  }, [participants]);

  return (
    <div>
      <div className="panel">
        <div className="panelHead">
          <h3>Konkurslar</h3>
          <p className="sub" style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
            Botda «Konkurs markazi» orqali foydalanuvchilar yoziladi; admin yakunda g‘olibni tasodifiy tanlaydi.
          </p>
        </div>
        {isAdmin && (
          <form className="contestForm" onSubmit={onCreate}>
            <input name="title" placeholder="Sarlavha" required />
            <input name="prize" placeholder="Mukofot" required />
            <input name="end_date" type="datetime-local" required />
            <button type="submit">Yangi konkurs</button>
          </form>
        )}
        <div className="tableWrap">
          <table className="data">
            <thead>
              <tr>
                <th>ID</th>
                <th>Sarlavha</th>
                <th>Mukofot</th>
                <th>Tugash</th>
                <th>Ishtirokchilar</th>
                <th>G‘olib</th>
                <th>Holat</th>
                {isAdmin && <th>Amallar</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const n = countByContest.get(r.id) ?? 0;
                const active = Boolean(r.is_active);
                return (
                  <tr key={r.id}>
                    <td className="mono">{r.id}</td>
                    <td>{r.title}</td>
                    <td>{r.prize}</td>
                    <td className="mono">{formatDate(r.end_date)}</td>
                    <td className="mono">{n}</td>
                    <td>
                      {r.winner_name ? (
                        <>
                          {r.winner_name}
                          {r.winner_telegram_id != null ? (
                            <span className="mono" style={{ display: "block", fontSize: 12 }}>
                              tg: {r.winner_telegram_id}
                            </span>
                          ) : null}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{statusBadge(active ? "active" : "closed")}</td>
                    {isAdmin && (
                      <td>
                        {active && n > 0 ? (
                          <button type="button" className="ghost" onClick={() => onPickWinner(r.id)}>
                            G‘olib tanlash
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <div className="panelHead">
          <h3>Qatnashuvchilar ({participants.length})</h3>
        </div>
        <div className="tableWrap">
          <table className="data">
            <thead>
              <tr>
                <th>ID</th>
                <th>Konkurs</th>
                <th>Mijoz</th>
                <th>Telegram</th>
                <th>Sana</th>
                <th>G‘olib</th>
              </tr>
            </thead>
            <tbody>
              {participants.map((p) => (
                <tr key={p.id}>
                  <td className="mono">{p.id}</td>
                  <td>{p.contest_title}</td>
                  <td>{p.full_name || "—"}</td>
                  <td className="mono">{p.telegram_id ?? "—"}</td>
                  <td className="mono">{formatDate(p.joined_at)}</td>
                  <td>{p.is_winner ? "⭐ Ha" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ClientSheet({ id, loading, data, onClose, canEdit, onUpdateClient }) {
  const c = data?.client;
  const [notesDraft, setNotesDraft] = useState("");
  const [statusDraft, setStatusDraft] = useState("");
  useEffect(() => {
    if (c) {
      setNotesDraft(c.notes || "");
      setStatusDraft(c.status || "new");
    }
  }, [c]);
  return (
    <div className="sheetBackdrop" role="presentation" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="sheetTop">
          <div>
            <h2 style={{ margin: 0, fontSize: "1.25rem" }}>Mijoz #{id}</h2>
            <p style={{ margin: "6px 0 0", color: "var(--muted)", fontSize: 13 }}>
              {loading ? "Yuklanmoqda…" : c ? `${c.full_name || "—"} · ${c.phone || "—"}` : "Topilmadi"}
            </p>
          </div>
          <button type="button" className="ghost" onClick={onClose}>
            Yopish
          </button>
        </div>
        <div className="sheetBody">
          {!loading && !c && <p style={{ color: "var(--muted)" }}>Ma’lumot yo‘q.</p>}
          {!loading && c && (
            <>
              <div className="sectionTitle">Asosiy</div>
              <dl className="kv">
                <dt>Telegram ID</dt>
                <dd>{c.telegram_id ?? "—"}</dd>
                <dt>Manba</dt>
                <dd>{c.source || "—"}</dd>
                <dt>Ro‘yxatdan</dt>
                <dd>{formatDate(c.created_at)}</dd>
              </dl>
              {canEdit && (
                <div style={{ marginTop: 14, display: "grid", gap: 10, maxWidth: 480 }}>
                  <label style={{ fontSize: 12, color: "var(--muted)" }}>Holat</label>
                  <select value={statusDraft} onChange={(e) => setStatusDraft(e.target.value)}>
                    {["new", "active", "vip", "blocked"].map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                  <label style={{ fontSize: 12, color: "var(--muted)" }}>Eslatmalar</label>
                  <textarea rows={3} value={notesDraft} onChange={(e) => setNotesDraft(e.target.value)} placeholder="Mijoz haqida…" />
                  <button type="button" onClick={() => onUpdateClient(id, { status: statusDraft, notes: notesDraft })}>
                    Saqlash
                  </button>
                </div>
              )}

              {data.telegram_user && (
                <>
                  <div className="sectionTitle">Telegram bot (users)</div>
                  <dl className="kv">
                    <dt>Taxallus</dt>
                    <dd>@{data.telegram_user.username || "—"}</dd>
                    <dt>Takliflar soni</dt>
                    <dd>{data.telegram_user.referrals_count ?? 0}</dd>
                    <dt>Kanal / IG</dt>
                    <dd>
                      {data.telegram_user.channel_ok ? "kanal ✓" : "kanal —"} ·{" "}
                      {data.telegram_user.instagram_ok ? "IG ✓" : "IG —"}
                    </dd>
                    <dt>TOP taxallus</dt>
                    <dd>{data.telegram_user.leaderboard_alias || "—"}</dd>
                  </dl>
                </>
              )}

              <div className="sectionTitle">E’lonlar (Telegram) — {data.listings?.length || 0}</div>
              <MiniTable
                headers={["#", "Mashina", "Narx", "Holat", "Sana"]}
                rows={(data.listings || []).map((x) => [
                  x.id,
                  `${x.brand} ${x.model}`,
                  formatUsd(x.price_ask_usd),
                  statusBadge(x.status),
                  formatDate(x.created_at),
                ])}
              />

              <div className="sectionTitle">Saqlangan qidiruvlar — {data.wishlists?.length || 0}</div>
              <MiniTable
                headers={["#", "Marka", "Model", "Yil", "Byudjet USD", "Faol"]}
                rows={(data.wishlists || []).map((w) => [
                  w.id,
                  w.brand,
                  w.model || "—",
                  `${w.year_min}–${w.year_max}`,
                  `${w.budget_min != null ? formatUsd(w.budget_min) : "—"} … ${formatUsd(w.budget_max)}`,
                  w.is_active ? "ha" : "yo‘q",
                ])}
              />

              <div className="sectionTitle">Konkurs ishtiroklari — {data.contest_participations?.length || 0}</div>
              <MiniTable
                headers={["Konkurs", "Sana", "G‘olib"]}
                rows={(data.contest_participations || []).map((p) => [
                  p.contest_title,
                  formatDate(p.joined_at),
                  p.is_winner ? "⭐" : "—",
                ])}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
