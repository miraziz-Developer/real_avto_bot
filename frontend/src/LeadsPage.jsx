import { useCallback, useEffect, useState } from "react";

const STATUS = {
  active: { label: "AI gaplashmoqda", cls: "b-muted" },
  handed_off: { label: "Menejer kutilmoqda", cls: "b-pending" },
  in_progress: { label: "Menejerda", cls: "b-approved" },
  won: { label: "Sotuv bo'ldi", cls: "b-approved" },
  lost: { label: "Yopilgan", cls: "b-rejected" },
};

const CHIPS = [
  { id: "open", label: "Ochiq" },
  { id: "handed_off", label: "Menejer kutilmoqda" },
  { id: "in_progress", label: "Menejerda" },
  { id: "active", label: "AI'da" },
  { id: "won", label: "Sotuv bo'ldi" },
  { id: "lost", label: "Yopilgan" },
  { id: "", label: "Hammasi" },
];

const ROLE = { user: "Mijoz", assistant: "AI", admin: "Menejer" };
const HOT = 60;

function usd(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `$${Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function when(v) {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleString("uz-UZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return String(v);
  }
}

function interest(l) {
  if (l.car_model) return `${[l.car_brand, l.car_model, l.car_year].filter(Boolean).join(" ")} · ${usd(l.car_price_usd)}`;
  return l.wants || "—";
}

function tgLink(l) {
  // Instagram mijozida telegram_id ustunida IGSID — Instagram profiliga havola
  if (l.channel === "instagram") return l.username ? `https://instagram.com/${l.username}` : null;
  return l.username ? `https://t.me/${l.username}` : `tg://user?id=${l.telegram_id}`;
}

function StatusBadge({ status }) {
  const s = STATUS[status] || { label: status || "—", cls: "b-muted" };
  return <span className={`badge ${s.cls}`}>{s.label}</span>;
}

export default function LeadsPage({ api, canEdit, query }) {
  const [filter, setFilter] = useState("open");
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
      const [l, s] = await Promise.all([api.get("/leads", { params }), api.get("/leads/stats")]);
      setList(l.data);
      setStats(s.data);
      setError("");
    } catch (e) {
      setError(e?.message || "Yuklab bo'lmadi");
    }
  }, [api, filter, page, query]);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [load]);

  const by = stats?.byStatus || {};
  const openCount = (by.active || 0) + (by.handed_off || 0) + (by.in_progress || 0);
  const cards = [
    { k: "Ochiq mijozlar", v: openCount, c: "c0" },
    { k: "Menejer kutmoqda", v: stats?.waitingForManager ?? 0, c: "c3" },
    { k: `Yangi (${stats?.days ?? 30} kun)`, v: stats?.created ?? 0, c: "c1" },
    { k: "Menejerga topshirilgan", v: stats?.handedOff ?? 0, c: "c0" },
    { k: "Sotuv bo'ldi", v: stats?.won ?? 0, c: "c2" },
    { k: "Konversiya", v: stats?.conversionPct == null ? "—" : `${stats.conversionPct}%`, c: "c2" },
    { k: "Topshirishgacha (daq)", v: stats?.avgMinutesToHandoff ?? "—", c: "c4" },
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

      <div className="panel">
        <div className="panelHead">
          <h3>AI suhbatlari va mijozlar ({list.total})</h3>
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
              </button>
            ))}
          </div>
        </div>
        <div className="tableWrap">
          <table className="data">
            <thead>
              <tr>
                <th>ID</th><th>Mijoz</th><th>Qiziqish</th><th>Byudjet</th><th>Ball</th><th>Holat</th><th>Oxirgi xabar</th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((l) => (
                <tr key={l.id} onClick={() => setOpenId(l.id)} style={{ cursor: "pointer" }}>
                  <td className="mono">{l.id}</td>
                  <td>
                    <strong>{l.name || "Mijoz"}</strong>
                    <div className="mono" style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
                      {[l.username ? `@${l.username}` : null, l.phone].filter(Boolean).join(" · ")}
                    </div>
                  </td>
                  <td>
                    {interest(l)}
                    {l.visit_time && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>📅 {l.visit_time}</div>}
                  </td>
                  <td className="mono">{usd(l.budget_usd)}</td>
                  <td className="mono">{l.score >= HOT ? "🔥 " : ""}{l.score}</td>
                  <td><StatusBadge status={l.status} /></td>
                  <td className="mono" style={{ fontSize: 12 }}>{when(l.last_message_at || l.created_at)}</td>
                </tr>
              ))}
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

      {openId && <LeadSheet id={openId} api={api} canEdit={canEdit} onClose={() => setOpenId(null)} onChanged={load} />}
    </div>
  );
}

function LeadSheet({ id, api, canEdit, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");

  const fetchLead = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get(`/leads/${id}`);
      setData(r.data);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api, id]);

  useEffect(() => {
    fetchLead();
  }, [fetchLead]);

  async function close(status) {
    try {
      await api.patch(`/leads/${id}`, { status });
      setMsg("Saqlandi ✓");
      await fetchLead();
      onChanged();
    } catch (e) {
      setMsg(e?.response?.data?.error || e?.message || "Xato");
    }
  }

  const l = data?.lead;
  const isOpen = l && ["active", "handed_off", "in_progress"].includes(l.status);

  return (
    <div className="sheetBackdrop" role="presentation" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="sheetTop">
          <div>
            <h2 style={{ margin: 0, fontSize: "1.25rem" }}>{l ? l.name || "Mijoz" : `Lead #${id}`}</h2>
            <p style={{ margin: "6px 0 0", color: "var(--muted)", fontSize: 13 }}>
              {loading ? "Yuklanmoqda…" : l ? <>Lead #{l.id} · <StatusBadge status={l.status} /> · ball {l.score}</> : "Topilmadi"}
            </p>
          </div>
          <button type="button" className="ghost" onClick={onClose}>Yopish</button>
        </div>
        <div className="sheetBody">
          {l && (
            <>
              <div className="sectionTitle">Mijoz</div>
              <dl className="kv">
                <dt>{l.channel === "instagram" ? "Instagram" : "Telegram"}</dt>
                <dd>
                  {tgLink(l) ? (
                    <a href={tgLink(l)} target="_blank" rel="noreferrer">{l.username ? `@${l.username}` : `id ${l.telegram_id}`}</a>
                  ) : "—"}
                </dd>
                <dt>Telefon</dt><dd>{l.phone ? <a href={`tel:${l.phone}`}>{l.phone}</a> : "—"}</dd>
                <dt>Qiziqish</dt><dd>{interest(l)}</dd>
                <dt>Byudjet / to'lov</dt><dd>{usd(l.budget_usd)} · {l.payment_method || "—"}</dd>
                <dt>Ko'rishga</dt><dd>{l.visit_time || "—"}</dd>
                <dt>Boshlangan</dt><dd>{when(l.created_at)}</dd>
                {l.handed_off_at && (<><dt>Menejerga topshirilgan</dt><dd>{when(l.handed_off_at)}</dd></>)}
              </dl>
              {l.summary && <p style={{ fontStyle: "italic", color: "var(--muted)" }}>📝 {l.summary}</p>}
              {l.handoff_reason && <p style={{ color: "var(--muted)", fontSize: 13 }}>Sabab: {l.handoff_reason}</p>}

              {canEdit && isOpen && (
                <div style={{ display: "flex", gap: 10, alignItems: "center", margin: "10px 0" }}>
                  <button type="button" onClick={() => close("won")}>🏁 Sotuv bo'ldi</button>
                  <button type="button" className="ghost" onClick={() => close("lost")}>Yopish</button>
                  {msg && <span style={{ color: "var(--muted)", fontSize: 13 }}>{msg}</span>}
                </div>
              )}
              <p style={{ color: "var(--muted)", fontSize: 12, margin: "4px 0 0" }}>
                Mijozga yozish — Telegram botdagi lead kartasiga reply qiling.
              </p>

              <div className="sectionTitle">Suhbat ({data.messages.length})</div>
              <div style={{ display: "grid", gap: 8 }}>
                {data.messages.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      justifySelf: m.role === "user" ? "start" : "end",
                      maxWidth: "85%",
                      background: m.role === "user" ? "var(--surface)" : "transparent",
                      border: "1px solid var(--border)",
                      borderRadius: 12,
                      padding: "8px 12px",
                    }}
                  >
                    <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                      {ROLE[m.role] || m.role} · {when(m.created_at)}
                    </div>
                    <div style={{ whiteSpace: "pre-wrap", fontSize: 14 }}>{m.content}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
