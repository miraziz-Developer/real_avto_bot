import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import CarsPage from "./CarsPage.jsx";
import LeadsPage from "./LeadsPage.jsx";

const baseURL = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "") || "/api";
const publicApi = axios.create({ baseURL });

const TABS = [
  { id: "dashboard", label: "Boshqaruv", ic: "◆" },
  { id: "leads", label: "Mijozlar (AI)", ic: "✦" },
  { id: "cars", label: "Mashinalar", ic: "▤" },
  { id: "clients", label: "Mijozlar", ic: "◎" },
  { id: "listings", label: "E'lonlar (TG)", ic: "▣" },
  { id: "wishlists", label: "Qidiruvlar (TG)", ic: "◇" },
  { id: "contest", label: "Konkurs", ic: "★" },
];

// Telefon (Telegram Mini App): pastki menyu — eng ko'p ishlatiladigan bo'limlar, qolgani «Ko'proq» ichida
const MOBILE_NAV = [
  { id: "dashboard", label: "Bosh", ic: "🏠" },
  { id: "cars", label: "Mashinalar", ic: "🚗" },
  { id: "leads", label: "Mijozlar", ic: "💬" },
  { id: "wishlists", label: "Qidiruv", ic: "🔔" },
  { id: "more", label: "Ko'proq", ic: "☰" },
];
const MORE_TABS = ["clients", "listings", "contest"];

const tg = typeof window !== "undefined" ? window.Telegram?.WebApp : undefined;

function haptic(kind = "light") {
  try {
    tg?.HapticFeedback?.impactOccurred(kind);
  } catch {
    /* Telegram'dan tashqarida — e'tiborsiz */
  }
}

// Telefonda jadval qatorlari kartochka bo'lib ko'rinadi: har katakka ustun nomi (data-label) yoziladi
function labelTables(root) {
  root.querySelectorAll("table.data").forEach((t) => {
    const heads = [...t.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    if (!heads.length) return;
    t.querySelectorAll("tbody tr").forEach((tr) => {
      [...tr.children].forEach((td, i) => {
        const label = td.colSpan > 1 ? "" : heads[i] ?? "";
        if (td.getAttribute("data-label") !== label) td.setAttribute("data-label", label);
      });
    });
  });
}

const BUYOUT_LABELS = {
  offered: "taklif yuborildi",
  accepted: "sotuvchi rozi",
  negotiating: "muhokamada",
  declined: "sotuvchi rad etdi",
  expired: "javob bo'lmadi",
  bought: "sotib olindi",
};

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

  const [moreOpen, setMoreOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  // Bosh sahifadagi kartochkadan bo'limga o'tish: qaysi filtr bilan ochilsin
  const [jump, setJump] = useState({ cars: "active", leads: "open" });

  // pagination state per tab
  const [pages, setPages] = useState({
    clients: { page: 1, limit: 50, total: 0, items: [] },
    listings: { page: 1, limit: 50, total: 0, items: [], filter: "" },
    wishlists: { page: 1, limit: 50, total: 0, items: [] },
    participants: { page: 1, limit: 50, total: 0, items: [] },
  });

  const [state, setState] = useState({
    stats: null,
    contests: [],
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

  const loadPaginated = useCallback(
    async (key, endpoint, opts = {}) => {
      const { page = 1, limit = 50, filterKey, filterValue } = opts;
      const params = { page, limit };
      if (filterKey && filterValue) params[filterKey] = filterValue;
      if (query) params.q = query;
      const { data } = await api.get(endpoint, { params });
      return data;
    },
    [api, query],
  );

  const load = useCallback(async () => {
    if (!token) return;
    const calls = [
      api.get("/stats"),
      api.get("/contest/history"),
      loadPaginated("clients", "/clients", { page: pages.clients.page, limit: pages.clients.limit }),
      loadPaginated("listings", "/listings", { page: pages.listings.page, limit: pages.listings.limit, filterKey: "status", filterValue: listingFilter }),
      loadPaginated("wishlists", "/wishlists", { page: pages.wishlists.page, limit: pages.wishlists.limit }),
      loadPaginated("participants", "/contest-participants", { page: pages.participants.page, limit: pages.participants.limit }),
    ];
    // Bitta so'rov xato bersa ham qolganlari ko'rinadi (avval butun sahifa yangilanmay qolardi)
    const res = await Promise.allSettled(calls);
    const unauthorized = res.some((r) => r.status === "rejected" && r.reason?.response?.status === 401);
    if (unauthorized) {
      localStorage.removeItem("crm_token");
      localStorage.removeItem("crm_user");
      setToken("");
      setUser(null);
      setError("Sessiya tugadi — qayta kiring.");
      return;
    }
    const val = (i, fallback) => (res[i].status === "fulfilled" ? res[i].value : fallback);
    const stats = val(0, null);
    const contests = val(1, null);
    setState((prev) => ({
      stats: stats ? stats.data : prev.stats,
      contests: contests ? contests.data : prev.contests,
    }));
    setPages((prev) => {
      const next = { ...prev };
      ["clients", "listings", "wishlists", "participants"].forEach((key, j) => {
        const d = val(j + 2, null);
        if (d) next[key] = { ...prev[key], items: d.items || [], total: d.total ?? 0 };
      });
      return next;
    });
    const failed = res.filter((r) => r.status === "rejected").length;
    setError(failed === res.length ? "Server bilan aloqa yo'q — internetni tekshiring." : "");
  }, [token, api, loadPaginated, pages.clients.page, pages.listings.page, pages.wishlists.page, pages.participants.page, pages.clients.limit, pages.listings.limit, pages.wishlists.limit, pages.participants.limit, listingFilter]);

  useEffect(() => {
    load();
    if (!token) return;
    const t = setInterval(load, 30000);
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

  const setPage = useCallback((key, page) => {
    setPages((prev) => ({ ...prev, [key]: { ...prev[key], page } }));
  }, []);

  // Telegram Mini App: mavzu ranglari, «orqaga» tugmasi ochiq oynani yopadi
  useEffect(() => {
    if (!tg?.initData) return;
    try {
      tg.setHeaderColor?.("#0c1222");
      tg.setBackgroundColor?.("#0c1222");
      tg.disableVerticalSwipes?.();
    } catch {
      /* eski Telegram versiyasi */
    }
    const onBack = () => {
      const sheets = document.querySelectorAll(".sheetBackdrop");
      const last = sheets[sheets.length - 1];
      if (last) last.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    };
    tg.BackButton?.onClick(onBack);
    const obs = new MutationObserver(() => {
      if (document.querySelector(".sheetBackdrop")) tg.BackButton?.show();
      else tg.BackButton?.hide();
    });
    obs.observe(document.body, { childList: true, subtree: true });
    return () => {
      obs.disconnect();
      tg.BackButton?.offClick(onBack);
    };
  }, []);

  useEffect(() => {
    const root = document.getElementById("root");
    if (!root) return undefined;
    labelTables(root);
    const obs = new MutationObserver(() => labelTables(root));
    obs.observe(root, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, []);

  function goTo(id, filters) {
    haptic();
    if (id === "more") {
      setMoreOpen(true);
      return;
    }
    if (filters) setJump((j) => ({ ...j, ...filters }));
    setTab(id);
    setMoreOpen(false);
    window.scrollTo({ top: 0 });
  }

  function logout() {
    localStorage.removeItem("crm_token");
    localStorage.removeItem("crm_user");
    setToken("");
    setUser(null);
  }

  // Telegram Mini App ichida ochilgan bo'lsa — admin parolsiz kiradi (initData imzosi backendda tekshiriladi)
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (!tg?.initData) return;
    tg.ready();
    tg.expand();
    if (token) return;
    publicApi
      .post("/auth/telegram", { init_data: tg.initData })
      .then((r) => {
        setToken(r.data.token);
        setUser(r.data.user);
        localStorage.setItem("crm_token", r.data.token);
        localStorage.setItem("crm_user", JSON.stringify(r.data.user));
        setError("");
      })
      .catch((e) => {
        setError(
          e?.response?.status === 403
            ? "Bu panel faqat Real Avto jamoasi uchun. Sizning Telegram akkauntingiz adminlar ro'yxatida yo'q."
            : "Telegram orqali kirib bo'lmadi — login va parol bilan kiring.",
        );
      });
  }, [token]);

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
    } catch (err) {
      if (err?.response?.status === 429) {
        const wait = Number(err.response.data?.retry_after || err.response.headers?.["retry-after"] || 0);
        const mins = wait > 0 ? Math.ceil(wait / 60) : 15;
        setError(`Juda ko‘p noto‘g‘ri urinish. ${mins} daqiqadan keyin qayta urinib ko‘ring.`);
        return;
      }
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

  const activeNav = MORE_TABS.includes(tab) ? "more" : tab;

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">RA</div>
          <div>
            <strong>Real Avto</strong>
            <span>CRM</span>
          </div>
        </div>
        <nav>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`navBtn ${tab === t.id ? "active" : ""}`}
              onClick={() => goTo(t.id)}
            >
              <span className="ic">{t.ic}</span>
              {t.label}
            </button>
          ))}
        </nav>
        <button type="button" className="ghost logoutBtn" onClick={logout}>Chiqish</button>
      </aside>

      <main className="content">
        <header className="topbar">
          <div className="titleBlock">
            <h2>{TABS.find((t) => t.id === tab)?.label}</h2>
            <p className="role">
              {user?.role === "admin" ? "Admin" : user?.role} · {user?.username}
            </p>
          </div>
          <div className="topActions">
            {tab !== "dashboard" && (
              <input
                className={`searchInput ${searchOpen ? "open" : ""}`}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && load()}
                placeholder="Qidiruv: ism, telefon, marka… (Enter)"
                type="search"
              />
            )}
            {tab !== "dashboard" && (
              <button type="button" className="ghost iconBtn mobileOnly" aria-label="Qidiruv" onClick={() => setSearchOpen((v) => !v)}>
                🔍
              </button>
            )}
            <button type="button" className="ghost iconBtn" aria-label="Yangilash" onClick={() => { haptic(); load(); }}>
              ↻<span className="desktopOnly"> Yangilash</span>
            </button>
          </div>
        </header>

        {tab === "dashboard" && (
          <Dashboard api={api} stats={state.stats} wishlists={pages.wishlists.items} onGo={goTo} />
        )}
        {tab === "leads" && (
          <LeadsPage key={`leads-${jump.leads}`} api={api} canEdit={canEdit} query={query} initialFilter={jump.leads} />
        )}
        {tab === "cars" && (
          <CarsPage key={`cars-${jump.cars}`} api={api} canEdit={canEdit} query={query} initialFilter={jump.cars} />
        )}
        {tab === "clients" && (
          <PaginatedTable
            title="Mijozlar"
            headers={["ID", "Ism", "Telefon", "Telegram ID", "Manba", "Holat"]}
            rows={pages.clients.items.map((r) => [
              <span className="mono">{r.id}</span>,
              r.full_name || "—",
              <span className="mono">{r.phone || "—"}</span>,
              <span className="mono">{r.telegram_id ?? "—"}</span>,
              statusBadge(r.source),
              statusBadge(r.status),
            ])}
            onRowClick={(i) => setClientSheetId(pages.clients.items[i].id)}
            page={pages.clients.page}
            limit={pages.clients.limit}
            total={pages.clients.total}
            onPageChange={(p) => setPage("clients", p)}
          />
        )}
        {tab === "listings" && (
          <ListingsPage
            rows={pages.listings.items}
            total={pages.listings.total}
            page={pages.listings.page}
            limit={pages.listings.limit}
            filter={listingFilter}
            onFilter={(f) => {
              setListingFilter(f);
              setPage("listings", 1);
            }}
            onPageChange={(p) => setPage("listings", p)}
            onOpenClient={(cid) => {
              setTab("clients");
              setClientSheetId(cid);
            }}
          />
        )}
        {tab === "wishlists" && (
          <WishlistsPage
            rows={pages.wishlists.items}
            total={pages.wishlists.total}
            page={pages.wishlists.page}
            limit={pages.wishlists.limit}
            onPageChange={(p) => setPage("wishlists", p)}
            onOpenClient={(cid) => {
              setTab("clients");
              setClientSheetId(cid);
            }}
          />
        )}
        {tab === "contest" && (
          <ContestPage
            rows={state.contests}
            participants={pages.participants.items}
            participantsTotal={pages.participants.total}
            participantsPage={pages.participants.page}
            participantsLimit={pages.participants.limit}
            onParticipantsPageChange={(p) => setPage("participants", p)}
            onCreate={createContest}
            onPickWinner={pickContestWinner}
            isAdmin={user?.role === "admin"}
          />
        )}

        {error && <p className="error">{error}</p>}
      </main>

      <nav className="bottomNav" aria-label="Bo'limlar">
        {MOBILE_NAV.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`bottomBtn ${activeNav === t.id ? "active" : ""}`}
            onClick={() => goTo(t.id)}
          >
            <span className="bIc">{t.ic}</span>
            <span className="bLbl">{t.label}</span>
          </button>
        ))}
      </nav>

      {moreOpen && (
        <div className="sheetBackdrop" role="presentation" onClick={(e) => e.target === e.currentTarget && setMoreOpen(false)}>
          <div className="sheet moreSheet" role="dialog" aria-modal="true">
            <div className="sheetTop">
              <h2 style={{ margin: 0, fontSize: "1.15rem" }}>Boshqa bo'limlar</h2>
              <button type="button" className="ghost" onClick={() => setMoreOpen(false)}>Yopish</button>
            </div>
            <div className="sheetBody moreList">
              {TABS.filter((t) => MORE_TABS.includes(t.id)).map((t) => (
                <button key={t.id} type="button" className="moreItem" onClick={() => goTo(t.id)}>
                  <span className="ic">{t.ic}</span>
                  <span>{t.label}</span>
                  <span className="chev">›</span>
                </button>
              ))}
              <button type="button" className="moreItem danger" onClick={logout}>
                <span className="ic">⎋</span>
                <span>Chiqish</span>
                <span className="chev">›</span>
              </button>
              <p className="role" style={{ marginTop: 14 }}>{user?.username}</p>
            </div>
          </div>
        </div>
      )}

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

function Dashboard({ api, stats, wishlists, onGo }) {
  const [cars, setCars] = useState(null);
  const [leads, setLeads] = useState(null);
  const [recent, setRecent] = useState([]);
  const [waiting, setWaiting] = useState([]);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      const r = await Promise.allSettled([
        api.get("/cars/stats"),
        api.get("/leads/stats"),
        api.get("/cars", { params: { status: "active", page: 1, limit: 6 } }),
        api.get("/leads", { params: { status: "handed_off", page: 1, limit: 5 } }),
      ]);
      if (!alive) return;
      if (r[0].status === "fulfilled") setCars(r[0].value.data);
      if (r[1].status === "fulfilled") setLeads(r[1].value.data);
      if (r[2].status === "fulfilled") setRecent(r[2].value.data.items || []);
      if (r[3].status === "fulfilled") setWaiting(r[3].value.data.items || []);
    };
    pull();
    const t = setInterval(pull, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [api]);

  // Statistikada 0 ta holat kaliti bo'lmaydi — yuklangan bo'lsa 0, yuklanmagan bo'lsa «—»
  const by = cars ? { active: 0, reserved: 0, review: 0, sold: 0, ...cars.byStatus } : {};
  const lb = leads?.byStatus || {};
  const kpis = [
    { k: "Sotuvda", v: by.active ?? "—", c: "c2", go: () => onGo("cars", { cars: "active" }) },
    { k: "Bron", v: by.reserved ?? "—", c: "c3", go: () => onGo("cars", { cars: "reserved" }) },
    { k: `Sotilgan (${cars?.days ?? 30} kun)`, v: cars?.soldCount ?? "—", c: "c4", go: () => onGo("cars", { cars: "sold" }) },
    { k: "Tekshiruv kutmoqda", v: by.review ?? "—", c: "c0", go: () => onGo("cars", { cars: "review" }), alert: (by.review || 0) > 0 },
    { k: "Menejer kutmoqda", v: leads?.waitingForManager ?? "—", c: "c3", go: () => onGo("leads", { leads: "handed_off" }), alert: (leads?.waitingForManager || 0) > 0 },
    { k: "Ochiq mijozlar", v: leads ? (lb.active || 0) + (lb.handed_off || 0) + (lb.in_progress || 0) : "—", c: "c0", go: () => onGo("leads", { leads: "open" }) },
    { k: "Konversiya", v: leads?.conversionPct == null ? "—" : `${leads.conversionPct}%`, c: "c2", go: () => onGo("leads", { leads: "won" }) },
    { k: "Faol qidiruvlar", v: stats?.wishlistsActive ?? "—", c: "c1", go: () => onGo("wishlists") },
  ];
  const recentWishlists = [...wishlists].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 5);

  return (
    <div className="dash">
      <div className="gridStats kpis">
        {kpis.map((x) => (
          <button key={x.k} type="button" className={`statCard ${x.c} ${x.alert ? "alert" : ""}`} onClick={x.go}>
            <div className="label">{x.k}</div>
            <div className="val">{x.v}</div>
          </button>
        ))}
      </div>

      <div className="split2">
        <div className="panel">
          <div className="panelHead">
            <h3>🔥 Menejer kutayotgan mijozlar</h3>
            <button type="button" className="linkBtn" onClick={() => onGo("leads", { leads: "handed_off" })}>Hammasi ›</button>
          </div>
          {waiting.length === 0 ? (
            <p className="empty">Hozir hech kim kutmayapti 👌</p>
          ) : (
            <div className="list">
              {waiting.map((l) => (
                <button key={l.id} type="button" className="listItem" onClick={() => onGo("leads", { leads: "handed_off" })}>
                  <div className="liMain">
                    <strong>{l.name || "Mijoz"}</strong>
                    <span>{l.car_model ? [l.car_brand, l.car_model, l.car_year].filter(Boolean).join(" ") : l.wants || "—"}</span>
                  </div>
                  <span className="liSide">{l.phone || (l.username ? `@${l.username}` : "")}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="panel">
          <div className="panelHead">
            <h3>🚗 Oxirgi qo'shilgan mashinalar</h3>
            <button type="button" className="linkBtn" onClick={() => onGo("cars", { cars: "active" })}>Hammasi ›</button>
          </div>
          {recent.length === 0 ? (
            <p className="empty">Sotuvda mashina yo'q</p>
          ) : (
            <div className="list">
              {recent.map((c) => (
                <button key={c.id} type="button" className="listItem" onClick={() => onGo("cars", { cars: "active" })}>
                  <div className="liMain">
                    <strong>{[c.brand, c.model, c.year].filter(Boolean).join(" ") || `#${c.id}`}</strong>
                    <span>{c.mileage_km != null ? `${Number(c.mileage_km).toLocaleString("ru-RU")} km` : "probeg —"}</span>
                  </div>
                  <span className="liSide mono">{formatUsd(c.price_usd)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panelHead">
          <h3>🔔 So'nggi qidiruvlar (mijoz kutayotgan mashinalar)</h3>
          <button type="button" className="linkBtn" onClick={() => onGo("wishlists")}>Hammasi ›</button>
        </div>
        {recentWishlists.length === 0 ? (
          <p className="empty">Qidiruv yo'q</p>
        ) : (
          <div className="list">
            {recentWishlists.map((r) => (
              <div key={r.id} className="listItem static">
                <div className="liMain">
                  <strong>{`${r.brand || ""} ${r.model || ""}`.trim() || "—"}</strong>
                  <span>{r.client_name || "—"}</span>
                </div>
                <span className="liSide mono">≤ {formatUsd(r.budget_max)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MiniTable({ headers, rows }) {
  return (
    <div className="tableWrap">
      <table className="data">
        <thead>
          <tr>{headers.map((h) => (<th key={h}>{h}</th>))}</tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={headers.length} style={{ color: "var(--muted)" }}>Ma’lumot yo‘q</td></tr>
          ) : (
            rows.map((r, i) => (
              <tr key={i}>{r.map((c, j) => (<td key={j}>{c}</td>))}</tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function Pagination({ page, limit, total, onPageChange }) {
  const totalPages = Math.max(1, Math.ceil(total / limit));
  if (totalPages <= 1) return null;
  const pages = [];
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== "...") {
      pages.push("...");
    }
  }
  return (
    <div className="pagination">
      <button type="button" className="ghost" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
        ← Oldingi
      </button>
      <div className="pageNums">
        {pages.map((p, idx) =>
          p === "..." ? (
            <span key={`dots-${idx}`} className="dots">…</span>
          ) : (
            <button
              key={p}
              type="button"
              className={p === page ? "active" : ""}
              onClick={() => onPageChange(p)}
            >
              {p}
            </button>
          ),
        )}
      </div>
      <button type="button" className="ghost" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
        Keyingi →
      </button>
      <span className="info">Jami {total} ta</span>
    </div>
  );
}

function PaginatedTable({ title, headers, rows, onRowClick, page, limit, total, onPageChange }) {
  return (
    <div className="panel">
      <div className="panelHead">
        <h3>{title} ({total})</h3>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>Qatorni bosing — to‘liq profil, e’lonlar, qidiruvlar</span>
      </div>
      <div className="tableWrap">
        <table className="data">
          <thead><tr>{headers.map((h) => (<th key={h}>{h}</th>))}</tr></thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={headers.length} style={{ color: "var(--muted)" }}>Ma’lumot yo‘q</td></tr>
            ) : (
              rows.map((r, i) => (
                <tr key={i} className={onRowClick ? "clickable" : ""} onClick={() => onRowClick && onRowClick(i)}>
                  {r.map((c, j) => (<td key={j}>{c}</td>))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <Pagination page={page} limit={limit} total={total} onPageChange={onPageChange} />
    </div>
  );
}

function ListingsPage({ rows, total, page, limit, filter, onFilter, onPageChange, onOpenClient }) {
  const chips = [
    { id: "", label: "Hammasi" },
    { id: "pending", label: "Moderatsiya" },
    { id: "approved", label: "Kanalda" },
    { id: "rejected", label: "Rad" },
  ];
  return (
    <div className="panel">
      <div className="panelHead">
        <h3>Telegram e’lonlari ({total})</h3>
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
              <th>ID</th><th>Mijoz</th><th>Mashina</th><th>Yil</th><th>Narx (USD)</th><th>Holat</th><th>Sana</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>
                  {r.client_name || "—"}
                  <div className="mono" style={{ opacity: 0.75, marginTop: 2 }}>{r.client_phone || ""}</div>
                </td>
                <td>
                  <strong>{r.brand} {r.model}</strong>
                  <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 2 }}>{r.mileage != null ? `${r.mileage} km` : ""}</div>
                </td>
                <td>{r.year}</td>
                <td className="mono">{formatUsd(r.price_ask_usd)}</td>
                <td>
                  {statusBadge(r.status)}
                  {r.buyout_status && (
                    <div style={{ fontSize: 11, marginTop: 4 }}>
                      💰 {BUYOUT_LABELS[r.buyout_status] || r.buyout_status} {r.buyout_price_usd ? formatUsd(r.buyout_price_usd) : ""}
                    </div>
                  )}
                  {String(r.status).toLowerCase() === "pending" && r.frozen_until && (
                    <div style={{ fontSize: 11, marginTop: 4, color: "var(--muted)" }}>⏳ {formatDate(r.frozen_until)} gacha</div>
                  )}
                </td>
                <td className="mono" style={{ fontSize: 12 }}>{formatDate(r.created_at)}</td>
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
      <Pagination page={page} limit={limit} total={total} onPageChange={onPageChange} />
    </div>
  );
}

function WishlistsPage({ rows, total, page, limit, onPageChange, onOpenClient }) {
  return (
    <div className="panel">
      <div className="panelHead">
        <h3>Saqlangan qidiruvlar — bot ({total})</h3>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          Foydalanuvchi kanalga mos e’lon bo‘lganda xabar oladi; CRMdan mijoz profiliga o‘tish mumkin.
        </span>
      </div>
      <div className="tableWrap">
        <table className="data">
          <thead>
            <tr>
              <th>ID</th><th>Mijoz</th><th>Marka / model</th><th>Yil</th><th>Byudjet (USD)</th><th>Holat</th><th>Sana</th><th></th>
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
                <td><strong>{r.brand}</strong> {r.model || ""}</td>
                <td className="mono">{r.year_min}–{r.year_max}</td>
                <td className="mono">{r.budget_min != null ? formatUsd(r.budget_min) : "—"} … {formatUsd(r.budget_max)}</td>
                <td>{r.is_active ? <span className="badge b-approved">faol</span> : <span className="badge b-muted">yopilgan</span>}</td>
                <td className="mono" style={{ fontSize: 12 }}>{formatDate(r.created_at)}</td>
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
      <Pagination page={page} limit={limit} total={total} onPageChange={onPageChange} />
    </div>
  );
}

function ContestPage({ rows, participants, participantsTotal, participantsPage, participantsLimit, onParticipantsPageChange, onCreate, onPickWinner, isAdmin }) {
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
                <th>ID</th><th>Sarlavha</th><th>Mukofot</th><th>Tugash</th><th>Ishtirokchilar</th><th>G‘olib</th><th>Holat</th>
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
                            <span className="mono" style={{ display: "block", fontSize: 12 }}>tg: {r.winner_telegram_id}</span>
                          ) : null}
                        </>
                      ) : "—"}
                    </td>
                    <td>{statusBadge(active ? "active" : "closed")}</td>
                    {isAdmin && (
                      <td>
                        {active && n > 0 ? (
                          <button type="button" className="ghost" onClick={() => onPickWinner(r.id)}>G‘olib tanlash</button>
                        ) : "—"}
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
        <div className="panelHead"><h3>Qatnashuvchilar ({participantsTotal})</h3></div>
        <div className="tableWrap">
          <table className="data">
            <thead>
              <tr><th>ID</th><th>Konkurs</th><th>Mijoz</th><th>Telegram</th><th>Sana</th><th>G‘olib</th></tr>
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
        <Pagination page={participantsPage} limit={participantsLimit} total={participantsTotal} onPageChange={onParticipantsPageChange} />
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
          <button type="button" className="ghost" onClick={onClose}>Yopish</button>
        </div>
        <div className="sheetBody">
          {!loading && !c && <p style={{ color: "var(--muted)" }}>Ma’lumot yo‘q.</p>}
          {!loading && c && (
            <>
              <div className="sectionTitle">Asosiy</div>
              <dl className="kv">
                <dt>Telegram ID</dt><dd>{c.telegram_id ?? "—"}</dd>
                <dt>Manba</dt><dd>{c.source || "—"}</dd>
                <dt>Ro‘yxatdan</dt><dd>{formatDate(c.created_at)}</dd>
              </dl>
              {canEdit && (
                <div style={{ marginTop: 14, display: "grid", gap: 10, maxWidth: 480 }}>
                  <label style={{ fontSize: 12, color: "var(--muted)" }}>Holat</label>
                  <select value={statusDraft} onChange={(e) => setStatusDraft(e.target.value)}>
                    {["new", "active", "vip", "blocked"].map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                  <label style={{ fontSize: 12, color: "var(--muted)" }}>Eslatmalar</label>
                  <textarea rows={3} value={notesDraft} onChange={(e) => setNotesDraft(e.target.value)} placeholder="Mijoz haqida…" />
                  <button type="button" onClick={() => onUpdateClient(id, { status: statusDraft, notes: notesDraft })}>Saqlash</button>
                </div>
              )}

              {data.telegram_user && (
                <>
                  <div className="sectionTitle">Telegram bot (users)</div>
                  <dl className="kv">
                    <dt>Taxallus</dt><dd>@{data.telegram_user.username || "—"}</dd>
                    <dt>Takliflar soni</dt><dd>{data.telegram_user.referrals_count ?? 0}</dd>
                    <dt>Kanal / IG</dt>
                    <dd>{data.telegram_user.channel_ok ? "kanal ✓" : "kanal —"} · {data.telegram_user.instagram_ok ? "IG ✓" : "IG —"}</dd>
                    <dt>TOP taxallus</dt><dd>{data.telegram_user.leaderboard_alias || "—"}</dd>
                  </dl>
                </>
              )}

              <div className="sectionTitle">E’lonlar (Telegram) — {data.listings?.length || 0}</div>
              <MiniTable
                headers={["#", "Mashina", "Narx", "Holat", "Sana"]}
                rows={(data.listings || []).map((x) => [
                  x.id, `${x.brand} ${x.model}`, formatUsd(x.price_ask_usd), statusBadge(x.status), formatDate(x.created_at),
                ])}
              />

              <div className="sectionTitle">Saqlangan qidiruvlar — {data.wishlists?.length || 0}</div>
              <MiniTable
                headers={["#", "Marka", "Model", "Yil", "Byudjet USD", "Faol"]}
                rows={(data.wishlists || []).map((w) => [
                  w.id, w.brand, w.model || "—", `${w.year_min}–${w.year_max}`,
                  `${w.budget_min != null ? formatUsd(w.budget_min) : "—"} … ${formatUsd(w.budget_max)}`, w.is_active ? "ha" : "yo‘q",
                ])}
              />

              <div className="sectionTitle">Konkurs ishtiroklari — {data.contest_participations?.length || 0}</div>
              <MiniTable
                headers={["Konkurs", "Sana", "G‘olib"]}
                rows={(data.contest_participations || []).map((p) => [
                  p.contest_title, formatDate(p.joined_at), p.is_winner ? "⭐" : "—",
                ])}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
