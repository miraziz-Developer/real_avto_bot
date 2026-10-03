import { useCallback, useEffect, useState } from "react";

const STATUS = {
  review: { label: "Tekshiruv", cls: "b-pending" },
  active: { label: "Sotuvda", cls: "b-approved" },
  reserved: { label: "Bron", cls: "b-pending" },
  sold: { label: "Sotildi", cls: "b-rejected" },
  archived: { label: "Arxiv", cls: "b-muted" },
};

const SOURCE = { channel: "Kanal", bot: "Bot e'loni", admin: "Admin", import: "Import" };

const EVENT_LABEL = {
  created: "Qo'shildi",
  price_changed: "Narx o'zgardi",
  status_changed: "Holat o'zgardi",
  edited: "Tahrirlandi",
};

const CHIPS = [
  { id: "active", label: "Sotuvda" },
  { id: "review", label: "Tekshiruv" },
  { id: "reserved", label: "Bron" },
  { id: "sold", label: "Sotilgan" },
  { id: "archived", label: "Arxiv" },
  { id: "", label: "Hammasi" },
];

function usd(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `$${Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function km(n) {
  return n === null || n === undefined ? "—" : `${Number(n).toLocaleString("ru-RU")} km`;
}

function date(v) {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleDateString("uz-UZ", { day: "2-digit", month: "2-digit", year: "numeric" });
  } catch {
    return String(v);
  }
}

function daysSince(v) {
  if (!v) return null;
  return Math.max(0, Math.floor((Date.now() - new Date(v).getTime()) / 86400000));
}

function title(c) {
  return [c.brand, c.model, c.year].filter(Boolean).join(" ") || `Mashina #${c.id}`;
}

function StatusBadge({ status }) {
  const s = STATUS[status] || { label: status || "—", cls: "b-muted" };
  return <span className={`badge ${s.cls}`}>{s.label}</span>;
}

function eventText(e) {
  const d = e.data || {};
  if (e.kind === "price_changed") return `${usd(d.old)} → ${usd(d.new)}`;
  if (e.kind === "status_changed") return `${STATUS[d.old]?.label || d.old || "—"} → ${STATUS[d.new]?.label || d.new}`;
  if (e.kind === "created") return `${SOURCE[d.source] || d.source || ""}${d.price_usd ? ` · ${usd(d.price_usd)}` : ""}`;
  const keys = Object.keys(d).filter((k) => k !== "actor");
  return keys.length ? keys.join(", ") : "";
}

export default function CarsPage({ api, canEdit, query }) {
  const [filter, setFilter] = useState("active");
  const [page, setPage] = useState(1);
  const [list, setList] = useState({ items: [], total: 0, limit: 50 });
  const [stats, setStats] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const params = { page, limit: 50 };
      if (filter) params.status = filter;
      if (query) params.q = query;
      const [l, s] = await Promise.all([api.get("/cars", { params }), api.get("/cars/stats")]);
      setList(l.data);
      setStats(s.data);
      setError("");
    } catch (e) {
      setError(e?.message || "Yuklab bo'lmadi");
    }
  }, [api, filter, page, query]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const by = stats?.byStatus || {};
  const cards = [
    { k: "Sotuvda", v: by.active ?? 0, c: "c2" },
    { k: "Sotuvdagi jami qiymat", v: usd(stats?.activeValueUsd), c: "c2" },
    { k: `Sotilgan (${stats?.days ?? 30} kun)`, v: stats?.soldCount ?? 0, c: "c4" },
    { k: "O'rtacha sotilish (kun)", v: stats?.avgDaysToSell ?? "—", c: "c1" },
    { k: "Tekshiruv kutmoqda", v: by.review ?? 0, c: "c0" },
    { k: `${stats?.staleDays ?? 14}+ kun turgan`, v: stats?.staleCount ?? 0, c: "c3" },
    { k: "Yangi qo'shilgan", v: stats?.newCount ?? 0, c: "c0" },
    { k: "Foyda (bizniki)", v: usd(stats?.ownProfitUsd), c: "c3" },
  ];
  const pages = Math.max(1, Math.ceil((list.total || 0) / (list.limit || 50)));

  return (
    <div>
      {error && <p className="error">{error}</p>}
      <div className="gridStats">
        {cards.map((x) => (
          <div key={x.k} className={`statCard ${x.c}`}>
            <div className="label">{x.k}</div>
            <div className="val">{x.v}</div>
          </div>
        ))}
      </div>

      {stats?.topSold?.length > 0 && (
        <div className="panel">
          <div className="panelHead"><h3>Eng ko'p sotilganlar ({stats.days} kun)</h3></div>
          <div className="chips" style={{ padding: "0 16px 16px" }}>
            {stats.topSold.map(([name, n]) => (
              <span key={name} className="chip">{name} — {n} ta</span>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panelHead">
          <h3>Mashinalar ({list.total})</h3>
          <div className="chips">
            {CHIPS.map((c) => (
              <button
                key={c.id || "all"}
                type="button"
                className={`chip ${filter === c.id ? "on" : ""}`}
                onClick={() => {
                  setFilter(c.id);
                  setPage(1);
                }}
              >
                {c.label}
                {c.id && by[c.id] != null ? ` (${by[c.id]})` : ""}
              </button>
            ))}
          </div>
        </div>
        <div className="tableWrap">
          <table className="data">
            <thead>
              <tr>
                <th>ID</th><th>Mashina</th><th>Probeg</th><th>Narx</th><th>Holat</th><th>Manba</th><th>Kanalda</th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((c) => {
                const d = daysSince(c.published_at);
                const stale = c.status === "active" && d !== null && d >= (stats?.staleDays ?? 14);
                return (
                  <tr key={c.id} onClick={() => setOpenId(c.id)} style={{ cursor: "pointer" }}>
                    <td className="mono">{c.id}</td>
                    <td>
                      <strong>{title(c)}</strong>
                      <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 2 }}>
                        {[c.transmission, c.fuel, c.color, c.position].filter(Boolean).join(" · ")}
                        {c.is_own ? " · 🏷 bizniki" : ""}
                      </div>
                    </td>
                    <td className="mono">{km(c.mileage_km)}</td>
                    <td className="mono">{usd(c.price_usd)}</td>
                    <td><StatusBadge status={c.status} /></td>
                    <td>{SOURCE[c.source] || c.source}</td>
                    <td className="mono" style={{ fontSize: 12, color: stale ? "var(--danger)" : undefined }}>
                      {d === null ? "—" : `${d} kun`}
                    </td>
                  </tr>
                );
              })}
              {!list.items.length && (
                <tr><td colSpan={7} style={{ color: "var(--muted)", textAlign: "center", padding: 24 }}>Hozircha yo'q</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {pages > 1 && (
          <div className="chips" style={{ padding: 12, justifyContent: "center" }}>
            <button type="button" className="chip" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Oldingi</button>
            <span className="chip">{page} / {pages}</span>
            <button type="button" className="chip" disabled={page >= pages} onClick={() => setPage(page + 1)}>Keyingi ›</button>
          </div>
        )}
      </div>

      {openId && (
        <CarSheet
          id={openId}
          api={api}
          canEdit={canEdit}
          onClose={() => setOpenId(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}

function CarSheet({ id, api, canEdit, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState({});
  const [msg, setMsg] = useState("");

  const fetchCar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get(`/cars/${id}`);
      setData(r.data);
      const c = r.data.car;
      setDraft({
        price_usd: c.price_usd ?? "",
        purchase_price_usd: c.purchase_price_usd ?? "",
        expenses_usd: c.expenses_usd ?? "",
        sold_price_usd: c.sold_price_usd ?? "",
      });
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api, id]);

  useEffect(() => {
    fetchCar();
  }, [fetchCar]);

  async function patch(body) {
    try {
      await api.patch(`/cars/${id}`, body);
      setMsg("Saqlandi ✓");
      await fetchCar();
      onChanged();
    } catch (e) {
      setMsg(e?.response?.data?.error || e?.message || "Xato");
    }
  }

  function saveMoney() {
    const body = {};
    for (const [k, v] of Object.entries(draft)) body[k] = v === "" ? null : Number(v);
    patch(body);
  }

  const c = data?.car;
  const profit =
    c?.is_own && c?.purchase_price_usd
      ? Number(c.sold_price_usd ?? c.price_usd ?? 0) - Number(c.purchase_price_usd) - Number(c.expenses_usd ?? 0)
      : null;

  return (
    <div className="sheetBackdrop" role="presentation" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="sheetTop">
          <div>
            <h2 style={{ margin: 0, fontSize: "1.25rem" }}>{c ? title(c) : `Mashina #${id}`}</h2>
            <p style={{ margin: "6px 0 0", color: "var(--muted)", fontSize: 13 }}>
              {loading ? "Yuklanmoqda…" : c ? <>#{c.id} · <StatusBadge status={c.status} /> · {SOURCE[c.source] || c.source}</> : "Topilmadi"}
            </p>
          </div>
          <button type="button" className="ghost" onClick={onClose}>Yopish</button>
        </div>
        <div className="sheetBody">
          {c && (
            <>
              {canEdit && (
                <>
                  <div className="sectionTitle">Holat</div>
                  <div className="chips">
                    {Object.entries(STATUS).map(([k, s]) => (
                      <button key={k} type="button" className={`chip ${c.status === k ? "on" : ""}`} onClick={() => patch({ status: k })}>
                        {s.label}
                      </button>
                    ))}
                  </div>
                </>
              )}

              <div className="sectionTitle">Ma'lumot</div>
              <dl className="kv">
                <dt>Narx</dt><dd>{usd(c.price_usd)}</dd>
                <dt>Probeg</dt><dd>{km(c.mileage_km)}</dd>
                <dt>Uzatma / yoqilg'i</dt><dd>{[c.transmission, c.fuel].filter(Boolean).join(" · ") || "—"}</dd>
                <dt>Rang / pozitsiya</dt><dd>{[c.color, c.position].filter(Boolean).join(" · ") || "—"}</dd>
                <dt>Kraska / DTP</dt>
                <dd>{c.paint_status || "—"} · {c.has_accident == null ? "DTP —" : c.has_accident ? "DTP bor" : "DTP yo'q"}</dd>
                <dt>Lokatsiya</dt><dd>{c.location || "—"}</dd>
                <dt>Kanalga chiqqan</dt><dd>{date(c.published_at)} ({daysSince(c.published_at) ?? "—"} kun)</dd>
                {c.sold_at && (<><dt>Sotilgan</dt><dd>{date(c.sold_at)}</dd></>)}
                {c.ai_confidence != null && (<><dt>Tahlil aniqligi</dt><dd>{Math.round(c.ai_confidence * 100)}%</dd></>)}
              </dl>
              {c.notes && <p style={{ fontStyle: "italic", color: "var(--muted)" }}>{c.notes}</p>}

              {canEdit && (
                <>
                  <div className="sectionTitle">Narx va foyda (USD)</div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, maxWidth: 640 }}>
                    {[
                      ["price_usd", "Sotuv narxi (e'lon)"],
                      ["purchase_price_usd", "Xarid narxi (bizniki bo'lsa)"],
                      ["expenses_usd", "Xarajatlar (ta'mir…)"],
                      ["sold_price_usd", "Haqiqiy sotilgan narx"],
                    ].map(([k, label]) => (
                      <label key={k} style={{ fontSize: 12, color: "var(--muted)", display: "grid", gap: 4 }}>
                        {label}
                        <input
                          type="number"
                          min="0"
                          value={draft[k]}
                          onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                        />
                      </label>
                    ))}
                  </div>
                  <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 10 }}>
                    <button type="button" onClick={saveMoney}>Saqlash</button>
                    {profit !== null && <span>Foyda: <b>{usd(profit)}</b></span>}
                    {msg && <span style={{ color: "var(--muted)", fontSize: 13 }}>{msg}</span>}
                  </div>
                </>
              )}

              {c.raw_text && (
                <>
                  <div className="sectionTitle">Kanaldagi matn</div>
                  <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, margin: 0 }}>{c.raw_text}</pre>
                </>
              )}

              <div className="sectionTitle">Tarix</div>
              <div className="tableWrap">
                <table className="data">
                  <tbody>
                    {(data.events || []).map((e) => (
                      <tr key={e.id}>
                        <td className="mono" style={{ fontSize: 12 }}>{date(e.created_at)}</td>
                        <td>{EVENT_LABEL[e.kind] || e.kind}</td>
                        <td>{eventText(e)}</td>
                        <td className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{e.data?.actor || (e.actor_telegram_id ? `tg:${e.actor_telegram_id}` : "")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
