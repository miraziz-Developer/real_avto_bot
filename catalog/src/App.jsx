import { useCallback, useEffect, useMemo, useState } from "react";

const API = "/api/public";
const tg = typeof window !== "undefined" ? window.Telegram?.WebApp : undefined;
const inTelegram = Boolean(tg?.initData);

const BUDGETS = [5000, 8000, 10000, 12000, 15000, 20000, 30000];
const YEARS = [2010, 2015, 2018, 2020, 2022, 2024];
const SORTS = [
  { id: "new", label: "Yangi qo'shilgan" },
  { id: "cheap", label: "Arzonroq" },
  { id: "expensive", label: "Qimmatroq" },
  { id: "year", label: "Yili yangi" },
];

function usd(n) {
  return n == null ? "Narx so'rang" : `$${Number(n).toLocaleString("en-US")}`;
}

function km(n) {
  return n == null ? null : `${Number(n).toLocaleString("ru-RU")} km`;
}

function title(c) {
  return [c.brand, c.model].filter(Boolean).join(" ") || "Mashina";
}

function openLink(url) {
  if (tg && url.startsWith("https://t.me/")) tg.openTelegramLink(url);
  else if (tg) tg.openLink(url);
  else window.open(url, "_blank", "noopener");
}

function useHashRoute() {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const m = hash.match(/^#\/car\/(\d+)/);
  return m ? { name: "car", id: Number(m[1]) } : { name: "home" };
}

async function getJSON(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export default function App() {
  const route = useHashRoute();
  const [meta, setMeta] = useState(null);

  useEffect(() => {
    if (tg) {
      tg.ready();
      tg.expand();
    }
    getJSON("/meta").then(setMeta).catch(() => setMeta({ business: { name: "Real Avto" }, brands: [] }));
  }, []);

  useEffect(() => {
    if (!tg) return undefined;
    const back = () => {
      window.location.hash = "";
    };
    if (route.name === "car") {
      tg.BackButton.show();
      tg.BackButton.onClick(back);
    } else {
      tg.BackButton.hide();
    }
    return () => tg.BackButton.offClick(back);
  }, [route.name]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [route.name, route.id]);

  const botLink = useCallback(
    (payload) => (meta?.bot_username ? `https://t.me/${meta.bot_username}${payload ? `?start=${payload}` : ""}` : null),
    [meta],
  );

  return (
    <div className="app">
      {route.name === "car" ? <CarPage id={route.id} meta={meta} botLink={botLink} /> : <Home meta={meta} botLink={botLink} />}
      <Footer meta={meta} />
    </div>
  );
}

function Home({ meta, botLink }) {
  const [filters, setFilters] = useState({ q: "", brand: "", price_max: "", year_min: "", transmission: "", sort: "new" });
  const [data, setData] = useState({ items: [], total: 0, page: 1 });
  const [loading, setLoading] = useState(true);
  const [qDraft, setQDraft] = useState("");

  const query = useMemo(() => {
    const p = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => v && p.set(k, v));
    return p;
  }, [filters]);

  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => ({ ...f, q: qDraft.trim() })), 350);
    return () => clearTimeout(t);
  }, [qDraft]);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    getJSON(`/cars?${query.toString()}`)
      .then((d) => !cancel && setData(d))
      .catch(() => !cancel && setData({ items: [], total: 0, page: 1 }))
      .finally(() => !cancel && setLoading(false));
    return () => {
      cancel = true;
    };
  }, [query]);

  async function loadMore() {
    const p = new URLSearchParams(query);
    p.set("page", String(data.page + 1));
    const d = await getJSON(`/cars?${p.toString()}`);
    setData((prev) => ({ ...d, items: [...prev.items, ...d.items] }));
  }

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const business = meta?.business || {};
  const sellLink = botLink("sell");

  return (
    <>
      <header className="hero">
        <div className="brandRow">
          <div className="logo">RA</div>
          <div>
            <h1>{business.name || "Real Avto"}</h1>
            <p className="muted">📍 {business.address || "Yangiyo'l"} · ishlatilgan mashinalar</p>
          </div>
        </div>
        <div className="heroActions">
          <a className="btn primary" href="#list" onClick={(e) => {
            e.preventDefault();
            document.getElementById("list")?.scrollIntoView({ behavior: "smooth" });
          }}>🚗 Mashina olaman</a>
          {sellLink && (
            <button type="button" className="btn" onClick={() => openLink(sellLink)}>💰 Mashina sotaman</button>
          )}
        </div>
      </header>

      <section id="list" className="filters">
        <input
          className="search"
          type="search"
          placeholder="Qidiruv: Cobalt, Gentra 2019…"
          value={qDraft}
          onChange={(e) => setQDraft(e.target.value)}
        />
        <div className="filterRow">
          <select value={filters.brand} onChange={set("brand")} aria-label="Marka">
            <option value="">Barcha markalar</option>
            {(meta?.brands || []).map((b) => (
              <option key={b.brand} value={b.brand}>{b.brand} ({b.count})</option>
            ))}
          </select>
          <select value={filters.price_max} onChange={set("price_max")} aria-label="Byudjet">
            <option value="">Har qanday narx</option>
            {BUDGETS.map((b) => <option key={b} value={b}>{usd(b)} gacha</option>)}
          </select>
          <select value={filters.year_min} onChange={set("year_min")} aria-label="Yili">
            <option value="">Har qanday yil</option>
            {YEARS.map((y) => <option key={y} value={y}>{y} dan yangi</option>)}
          </select>
          <select value={filters.transmission} onChange={set("transmission")} aria-label="Uzatma">
            <option value="">Avtomat / mexanika</option>
            <option value="avtomat">Avtomat</option>
            <option value="mexanika">Mexanika</option>
          </select>
          <select value={filters.sort} onChange={set("sort")} aria-label="Saralash">
            {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </div>
        <p className="muted count">{loading ? "Yuklanmoqda…" : `Sotuvda: ${data.total} ta mashina`}</p>
      </section>

      {!loading && data.items.length === 0 ? (
        <Empty filters={filters} botLink={botLink} />
      ) : (
        <section className="grid">
          {data.items.map((c) => <CarCard key={c.id} car={c} />)}
        </section>
      )}
      {data.items.length < data.total && (
        <div className="center">
          <button type="button" className="btn" onClick={loadMore}>Yana ko'rsatish</button>
        </div>
      )}
    </>
  );
}

function Photo({ car, idx = 0, className = "" }) {
  const [failed, setFailed] = useState(false);
  if (!car.photos_count || failed) {
    return (
      <div className={`photo placeholder ${className}`}>
        <span>🚗</span>
      </div>
    );
  }
  return (
    <img
      className={`photo ${className}`}
      src={`${API}/photo/${car.id}/${idx}`}
      alt={`${title(car)} ${car.year || ""}`}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

function InsightBadge({ insight }) {
  if (!insight) return null;
  if (insight.kind === "cheaper") return <span className="badge good">Bozordan ~{insight.pct}% arzon</span>;
  return <span className="badge fair">Bozor narxida</span>;
}

function CarCard({ car }) {
  return (
    <a className="card" href={`#/car/${car.id}`}>
      <div className="photoWrap">
        <Photo car={car} />
        {car.reserved && <span className="badge reserved floating">Bron</span>}
      </div>
      <div className="cardBody">
        <div className="price">{usd(car.price_usd)}</div>
        <div className="cardTitle">{title(car)} {car.year || ""}</div>
        <div className="muted small">{[km(car.mileage_km), car.transmission, car.fuel].filter(Boolean).join(" · ")}</div>
        <InsightBadge insight={car.insight} />
      </div>
    </a>
  );
}

function Empty({ filters, botLink }) {
  return (
    <section className="empty">
      <p>Hozircha mos mashina yo'q 😔</p>
      <AlertForm defaults={{ brand: filters.brand, budget: filters.price_max }} botLink={botLink} />
    </section>
  );
}

function AlertForm({ defaults = {}, botLink }) {
  const [brand, setBrand] = useState(defaults.brand || "");
  const [model, setModel] = useState("");
  const [budget, setBudget] = useState(defaults.budget || "");
  const [state, setState] = useState("");

  if (!inTelegram) {
    const link = botLink("alert");
    return link ? (
      <div className="alertBox">
        <p>🔔 Kerakli mashina kanalga chiqishi bilan sizga Telegram'da xabar beramiz.</p>
        <button type="button" className="btn primary" onClick={() => openLink(link)}>Botda qidiruvni saqlash</button>
      </div>
    ) : null;
  }

  async function submit(e) {
    e.preventDefault();
    if (!brand.trim()) {
      setState("Markani yozing");
      return;
    }
    setState("…");
    try {
      const r = await fetch(`${API}/alerts`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-telegram-init-data": tg.initData },
        body: JSON.stringify({ brand, model, budget_max_usd: budget ? Number(budget) : undefined }),
      });
      if (r.status === 409) {
        setState("Sizda allaqachon 5 ta qidiruv bor");
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      setState("✅ Saqlandi! Mos mashina chiqsa, botga xabar keladi.");
      tg.HapticFeedback?.notificationOccurred("success");
    } catch {
      setState("Xatolik, keyinroq urinib ko'ring");
    }
  }

  return (
    <form className="alertBox" onSubmit={submit}>
      <p>🔔 <b>Chiqsa xabar beraylikmi?</b> Shunday mashina kanalga tushishi bilan sizga yozamiz.</p>
      <input placeholder="Marka (masalan Chevrolet)" value={brand} onChange={(e) => setBrand(e.target.value)} />
      <input placeholder="Model (ixtiyoriy, masalan Spark)" value={model} onChange={(e) => setModel(e.target.value)} />
      <input
        placeholder="Byudjet, $ (ixtiyoriy)"
        inputMode="numeric"
        value={budget}
        onChange={(e) => setBudget(e.target.value.replace(/\D/g, ""))}
      />
      <button type="submit" className="btn primary">Saqlash</button>
      {state && <p className="muted">{state}</p>}
    </form>
  );
}

function CarPage({ id, meta, botLink }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setData(null);
    setError(false);
    getJSON(`/cars/${id}`).then(setData).catch(() => setError(true));
  }, [id]);

  if (error) {
    return (
      <section className="empty">
        <p>Bu mashina endi sotuvda yo'q.</p>
        <a className="btn primary" href="#">Boshqa mashinalarni ko'rish</a>
      </section>
    );
  }
  if (!data) return <p className="center muted">Yuklanmoqda…</p>;

  const c = data.car;
  const rate = meta?.usd_rate_uzs;
  const askLink = botLink(`car_${c.id}`);
  const phone = meta?.business?.phones?.[0];
  const mapUrl = meta?.business?.map_url;
  const specs = [
    ["Yili", c.year],
    ["Probeg", km(c.mileage_km)],
    ["Uzatma", c.transmission],
    ["Yoqilg'i", c.fuel],
    ["Rang", c.color],
    ["Pozitsiya", c.position],
    ["Kraska", c.paint_status],
    ["DTP", c.has_accident == null ? null : c.has_accident ? "bor" : "yo'q"],
    ["Joylashuv", c.location],
  ].filter(([, v]) => v);

  return (
    <article className="carPage">
      {!inTelegram && <a className="back" href="#">← Barcha mashinalar</a>}
      <div className="gallery">
        {c.photos_count > 0
          ? Array.from({ length: Math.min(c.photos_count, 10) }, (_, i) => (
              <Photo key={i} car={c} idx={i} className="galleryPhoto" />
            ))
          : <Photo car={c} className="galleryPhoto" />}
      </div>
      <div className="carHead">
        <h2>{title(c)} {c.year || ""}</h2>
        <div className="bigPrice">{usd(c.price_usd)}</div>
        {rate && c.price_usd && (
          <div className="muted small">≈ {Math.round((c.price_usd * rate) / 1_000_000)} mln so'm</div>
        )}
        <div className="badges">
          {c.reserved && <span className="badge reserved">Hozir bron qilingan</span>}
          <InsightBadge insight={c.insight} />
        </div>
        {c.insight && (
          <p className="muted small">
            Bizning bazadagi {c.insight.comparables} ta o'xshash mashina narxi bo'yicha bozor narxi ≈ {usd(c.insight.median)}
          </p>
        )}
      </div>

      <div className="actions">
        {askLink && (
          <button type="button" className="btn primary" onClick={() => openLink(askLink)}>
            🤖 Savol berish / ko'rishga yozilish
          </button>
        )}
        {phone && <a className="btn" href={`tel:${phone.replace(/[^\d+]/g, "")}`}>📞 Qo'ng'iroq qilish</a>}
        {mapUrl && <button type="button" className="btn" onClick={() => openLink(mapUrl)}>📍 Xaritada</button>}
      </div>

      <dl className="specs">
        {specs.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {c.notes && <p className="notes">{c.notes}</p>}
      {c.days_on_sale != null && <p className="muted small">Sotuvga chiqqaniga {c.days_on_sale} kun bo'ldi</p>}

      {data.similar.length > 0 && (
        <>
          <h3>O'xshash mashinalar</h3>
          <section className="grid">
            {data.similar.map((s) => <CarCard key={s.id} car={s} />)}
          </section>
        </>
      )}
    </article>
  );
}

function Footer({ meta }) {
  const b = meta?.business || {};
  return (
    <footer className="footer muted small">
      <div>{b.name || "Real Avto"} · {b.address || "Yangiyo'l"}</div>
      {b.hours && <div>🕘 {b.hours}</div>}
      {b.phones?.length > 0 && (
        <div className="phones">
          {b.phones.map((p) => (
            <a key={p} href={`tel:${p.replace(/[^\d+]/g, "")}`}>{p}</a>
          ))}
        </div>
      )}
    </footer>
  );
}
