/* Vitta-Mantri UI. Builds the DOM with textContent only (no innerHTML) so scraped/LLM text cannot inject markup.
   Works with the existing /api/briefing + /stream payload; uses optional new fields when present:
   board[].chart{verdict,score,reasons,plan|entry_zone,stop,target,rr}, news_action, priced_in, counter_view.
   Preview without a backend: open index.html?demo=1 */
(() => {
"use strict";
const $ = (i) => document.getElementById(i);
const mk = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
const num = (v, d = 0) => (Number.isFinite(Number(v)) ? Number(v) : d);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage may be blocked */ } },
};
const DEMO = /[?&]demo=1/.test(location.search);
const STALE_MIN = 10, GLYPH = { BUY: "▲", SELL: "▼", WATCH: "◆" };
let DATA = {}, PREV = null, lastFocus = null;
const V = { tab: "signals", side: "ALL", region: "all", src: new Set(), direct: false, q: "", confirmed: false };

function ago(iso) {
  if (!iso) return "–";
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z"), s = (Date.now() - t) / 1000;
  if (!(s >= 0)) return "just now";
  return s < 60 ? "just now" : s < 3600 ? Math.floor(s / 60) + "m ago" : s < 86400 ? Math.floor(s / 3600) + "h ago" : Math.floor(s / 86400) + "d ago";
}
function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 2400); }

/* keyed reconcile: updates changed nodes in place, keeps scroll/hover/focus stable across SSE pushes */
function sync(parent, items, key, make, fill) {
  const old = new Map();
  for (const c of parent.children) old.set(c.dataset.k, c);
  items.forEach((it, i) => {
    const k = String(key(it));
    let n = old.get(k); const fresh = !n;
    if (n) old.delete(k); else { n = make(); n.dataset.k = k; }
    const sig = JSON.stringify(it, (kk, v) => (kk === "headlines" ? v.length : v));
    if (n._sig !== sig) {
      fill(n, it); n._sig = sig;
      if (!fresh) { n.classList.remove("flash"); void n.offsetWidth; n.classList.add("flash"); }
    }
    if (parent.children[i] !== n) parent.insertBefore(n, parent.children[i] || null);
  });
  old.forEach((n) => n.remove());
}

/* ---------- signals ---------- */
const chip = (a, net) => {
  const c = mk("span", "chip " + a.toLowerCase(), `${GLYPH[a] || ""} ${a}`);
  if (a !== "WATCH" && net != null) c.append(mk("em", "", (num(net) > 0 ? "+" : "") + num(net).toFixed(1)));
  return c;
};
const boardRows = () => (DATA.board || [])
  .filter((r) => (V.side === "ALL" || r.action === V.side) && (!V.confirmed || (r.chart && r.chart.verdict === "CONFIRM")))
  .sort((a, b) => num(b.conviction) - num(a.conviction) || Math.abs(num(b.net)) - Math.abs(num(a.net)));

function fillRow(n, r) {
  const a = r.action || "WATCH", conv = Math.max(0, Math.min(5, Math.round(num(r.conviction))));
  n.className = "row " + a.toLowerCase();
  const head = mk("div", "rh"); head.append(mk("b", "tk", r.ticker), chip(a, r.net));
  const meter = mk("div", "meter"); meter.setAttribute("role", "img"); meter.setAttribute("aria-label", `Conviction ${conv} of 5`);
  for (let i = 0; i < 5; i++) meter.append(mk("i", i < conv ? "on" : ""));
  const left = mk("div", "rl"); left.append(head, meter);
  const hop = num(r.direct_buy) + num(r.direct_sell) >= num(r.indirect_buy) + num(r.indirect_sell) ? "direct" : "indirect";
  const meta = mk("div", "meta");
  meta.append(mk("span", "", hop), mk("span", "", `${(r.headlines || []).length} prints`), mk("span", "", `${(r.sources || []).length} sources`));
  const flags = mk("div", "flags");
  if (r.chart && r.chart.verdict) flags.append(mk("span", "vchip v-" + r.chart.verdict.toLowerCase(), "Chart: " + r.chart.verdict.toLowerCase()));
  if (r.news_action && r.news_action !== a) flags.append(mk("span", "vchip v-note", "News said " + r.news_action));
  if (r.priced_in) flags.append(mk("span", "vchip v-wait", "Priced in"));
  const mid = mk("div", "rm"); mid.append(mk("p", "why", r.why || "Aggregated from live headlines."), meta, flags);
  n.replaceChildren(left, mid, mk("span", "chev", "›"));
}
function renderSignals() {
  const rows = boardRows();
  sync($("list"), rows, (r) => r.ticker, () => { const d = mk("div"); d.tabIndex = 0; d.setAttribute("role", "button"); return d; }, fillRow);
  $("emptyS").hidden = rows.length > 0;
}

/* ---------- drawer ---------- */
const kv = (k, v) => { const d = mk("div", "kv"); d.append(mk("span", "", k), mk("b", "", v)); return d; };
const section = (title, ...kids) => { const s = mk("div", "sec"); s.append(mk("h4", "", title), ...kids); return s; };
function openDrawer(ticker) {
  const r = (DATA.board || []).find((x) => x.ticker === ticker);
  if (!r) return;
  lastFocus = document.activeElement;
  $("dTitle").textContent = r.ticker;
  const body = $("dBody"), a = r.action || "WATCH", urls = new Map((DATA.news || []).map((n) => [n.title, n.url]));
  const top = mk("div", "rh"); top.append(chip(a, r.net), mk("span", "meta", `conviction ${num(r.conviction)}/5`));
  const why = mk("ul"); (r.whys && r.whys.length ? r.whys : [r.why || "Aggregated from live headlines."]).slice(0, 6).forEach((w) => why.append(mk("li", "", w)));
  const ev = mk("ul");
  (r.headlines || []).slice(0, 8).forEach((h) => { const li = mk("li"), l = mk("a", "", h); l.href = safeUrl(urls.get(h)); l.target = "_blank"; l.rel = "noopener noreferrer"; li.append(l); ev.append(li); });
  const ch = r.chart || {}, plan = ch.plan || ch, zone = plan.entry_zone;
  const grid = mk("div", "grid");
  if (ch.verdict) {
    grid.append(kv("Chart verdict", `${ch.verdict}${ch.score != null ? " (" + ch.score + ")" : ""}`),
      kv("Entry zone", zone && zone.length === 2 ? `${zone[0]} – ${zone[1]}` : "none"),
      kv("Stop", plan.stop ?? "–"), kv("Target", plan.target ?? "–"), kv("Risk : reward", plan.rr ?? "–"),
      kv("News said", r.news_action || a));
  }
  const chartBox = mk("div"); chartBox.id = "chartBox"; chartBox.textContent = "Loading chart…";
  const parts = [top, section("Why", why), section("Evidence", ev.children.length ? ev : mk("p", "meta", "No linked headlines.")), section("Chart & price range", chartBox)];
  if (ch.verdict) { parts.push(grid); const rs = mk("ul"); (ch.reasons || []).forEach((x) => rs.append(mk("li", "", x))); parts.push(section("Chart reasoning", rs)); }
  if (r.counter_view) parts.push(section("Counter-view", mk("p", "", r.counter_view)));
  parts.push(mk("p", "disclaimer", "Educational impact map — not investment advice."));
  body.replaceChildren(...parts);
  $("drawer").hidden = false; $("main").inert = true; $("top").inert = true; $("sheet").focus();
  loadChart(r, chartBox);
}
function closeDrawer() {
  if ($("drawer").hidden) return;
  $("drawer").hidden = true; $("main").inert = false; $("top").inert = false;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}
function loadChart(r, box) {
  const act = r.action === "SELL" ? "SELL" : "BUY";
  const done = (p) => { if ($("chartBox") === box && window.renderChart) window.renderChart(box, p); };
  if (DEMO) return done(demoChart(r.action));
  fetch(`/api/chart/${encodeURIComponent(r.ticker)}?action=${act}`)
    .then((x) => (x.ok ? x.json() : Promise.reject()))
    .then(done)
    .catch(() => { if ($("chartBox") === box) box.textContent = "Chart unavailable — configure a market-data provider."; });
}

/* ---------- tape ---------- */
const LEGS = [["direct_buy", "Direct buy", "buy"], ["direct_sell", "Direct sell", "sell"], ["indirect_buy", "Indirect buy", "buy"], ["indirect_sell", "Indirect sell", "sell"]];
function newsList() {
  const q = V.q.trim().toLowerCase();
  return (DATA.news || []).filter((n) => {
    if (V.region !== "all" && !(n.region || "").toLowerCase().includes(V.region)) return false;
    if (V.src.size && !V.src.has(n.source)) return false;
    if (V.direct && !((n.direct_buy || []).length + (n.direct_sell || []).length)) return false;
    if (q) {
      const tk = LEGS.flatMap(([k]) => (n[k] || []).map((s) => s.ticker));
      if (![n.title, n.meaning, n.theme, n.source, ...tk].join(" ").toLowerCase().includes(q)) return false;
    }
    return true;
  }).slice(0, 60);
}
function fillCard(n, it) {
  n.className = "card";
  const t = mk("div", "top2");
  t.append(mk("span", "", `${it.region || ""} · ${it.source} · grade ${it.source_grade ?? "–"}`), mk("span", "", ago(it.published_at || it.fetched_at)),
    mk("span", "", it.theme || ""), mk("span", "badge" + (it.interpreted_by === "ai" ? " ai" : ""), it.interpreted_by === "ai" ? "AI" : "rules"));
  const h = mk("h3"), l = mk("a", "", it.title); l.href = safeUrl(it.url); l.target = "_blank"; l.rel = "noopener noreferrer"; h.append(l);
  const m = mk("p", "meaning", it.meaning || ""); m.title = "Click to expand";
  const legs = mk("div", "legs");
  LEGS.forEach(([k, label, cls]) => {
    const list = it[k] || []; if (!list.length) return;
    const g = mk("div", "leg"); g.append(mk("span", "", label));
    list.slice(0, 8).forEach((s) => { const b = mk("button", "tag " + cls, s.ticker); b.type = "button"; b.dataset.t = s.ticker; g.append(b); });
    legs.append(g);
  });
  n.replaceChildren(t, h, m, legs);
}
function renderTape() {
  const items = newsList();
  sync($("feed"), items, (n) => n.id || n.url || n.title, () => mk("article"), fillCard);
  $("emptyT").hidden = items.length > 0;
  const names = [...new Set([...(DATA.news || []).map((n) => n.source), ...(DATA.coverage || []).map((c) => c.source)])].filter(Boolean).sort();
  const pop = $("srcPop");
  if (pop.dataset.n !== names.join("|")) {
    pop.dataset.n = names.join("|");
    pop.replaceChildren(...names.map((s) => { const lb = mk("label"), cb = mk("input"); cb.type = "checkbox"; cb.value = s; cb.checked = V.src.has(s); lb.append(cb, mk("span", "", s)); return lb; }));
  }
  $("srcCount").textContent = V.src.size ? V.src.size + " selected" : "all";
}

/* ---------- kpis, sources, status ---------- */
function renderKpis() {
  const c = { BUY: 0, SELL: 0, WATCH: 0 };
  (DATA.board || []).forEach((r) => { if (c[r.action] != null) c[r.action]++; });
  const cov = DATA.coverage || [], live = cov.filter((x) => num(x.raw) > 0).length, dec = num(DATA.count, (DATA.news || []).length);
  const cards = [["Decoded", dec, "dec"], ["Buy", c.BUY, "buy"], ["Sell", c.SELL, "sell"], ["Watch", c.WATCH, "watch"],
    ["Sources live", `${live}/${cov.length || "–"}`, "src"], [DATA.ai_ready ? "Interpreter" : "Mode", DATA.ai_ready ? DATA.interpreter || "AI" : "Rules", "mode"]];
  const prev = PREV;
  $("kpis").replaceChildren(...cards.map(([k, v, cls]) => {
    const d = mk("div", "kpi " + cls); d.append(mk("span", "", k), mk("b", "", v));
    if (prev && typeof v === "number" && prev[k] != null && v !== prev[k]) d.append(mk("em", v > prev[k] ? "up" : "down", (v > prev[k] ? "+" : "") + (v - prev[k])));
    return d;
  }));
  PREV = { Decoded: dec, Buy: c.BUY, Sell: c.SELL, Watch: c.WATCH };
}
function renderSources() {
  $("srcBody").replaceChildren(...(DATA.coverage || []).map((c) => {
    const tr = mk("tr"), ok = num(c.raw) > 0, st = mk("td"), d = mk("span", "dot" + (ok ? " ok" : ""));
    st.append(d, document.createTextNode(ok ? "Live" : "No items"));
    tr.append(mk("td", "", c.source), mk("td", "", num(c.raw)), st); return tr;
  }));
}
function setLive(on, text) { $("live").classList.toggle("on", on); $("liveText").textContent = text; }
function tick() {
  const u = DATA.updated_at, stale = u && (Date.now() - Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(u) ? u : u + "Z")) / 60000 > STALE_MIN;
  const b = $("stale"); b.hidden = !stale;
  if (stale) b.textContent = `Data is stale — last update ${ago(u)}. Signals may no longer reflect the market.`;
  if (u && $("live").classList.contains("on")) $("liveText").textContent = "live · " + ago(u);
}
function renderAll() { renderKpis(); renderSignals(); if (V.tab === "tape") renderTape(); if (V.tab === "sources") renderSources(); }
function ingest(snap) {
  if (!snap) return;
  DATA = snap; $("skel").hidden = true; renderAll(); tick();
}
function setTab(t) {
  V.tab = t;
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
  ["signals", "tape", "sources"].forEach((v) => ($("v-" + v).hidden = v !== t));
  renderAll();
}

/* ---------- actions ---------- */
async function refresh() {
  const btn = $("refresh"); if (DEMO) return toast("Demo mode");
  btn.disabled = true; btn.textContent = "Refreshing…";
  const call = (tok) => fetch("/api/refresh", { method: "POST", headers: tok ? { "X-Admin-Token": tok } : {} });
  try {
    let tok = ""; try { tok = sessionStorage.getItem("vm-admin") || ""; } catch { /* ignore */ }
    let r = await call(tok);
    if (r.status === 405) r = await fetch("/api/refresh");           // pre-hardening backend
    if (r.status === 401) { tok = prompt("Admin token") || ""; if (tok) { try { sessionStorage.setItem("vm-admin", tok); } catch { /* ignore */ } r = await call(tok); } }
    if (!r.ok) throw new Error(r.status); ingest(await r.json()); toast("Updated");
  } catch { toast("Refresh failed"); } finally { btn.disabled = false; btn.textContent = "Refresh"; }
}
function applyTheme(t) { document.documentElement.dataset.theme = t; store.set("vm-theme", t); }

/* command palette */
const pal = $("pal"), pq = $("palQ"), pl = $("palList"); let palSel = 0, palItems = [];
function palRender() {
  const q = pq.value.trim().toLowerCase(); palItems = [];
  (DATA.board || []).forEach((r) => { if (!q || r.ticker.toLowerCase().includes(q)) palItems.push({ l: r.ticker, s: `${r.action} · ${num(r.conviction)}/5`, go: () => openDrawer(r.ticker) }); });
  if (q) (DATA.news || []).forEach((n) => { if ((n.title || "").toLowerCase().includes(q)) palItems.push({ l: n.title, s: n.source, go: () => { V.q = q; $("q").value = q; setTab("tape"); } }); });
  palItems = palItems.slice(0, 8); palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  pl.replaceChildren(...palItems.map((it, i) => { const li = mk("li", i === palSel ? "sel" : ""); li.append(mk("b", "", it.l), mk("small", "", it.s)); li.addEventListener("click", () => { pal.close(); it.go(); }); return li; }));
}
function openPal() { pq.value = ""; palSel = 0; palRender(); pal.showModal(); pq.focus(); }

/* ---------- events ---------- */
$("tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setTab(b.dataset.tab); });
$("side").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; V.side = b.dataset.s; document.querySelectorAll("#side button").forEach((x) => x.classList.toggle("on", x === b)); renderSignals(); });
$("region").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; V.region = b.dataset.r; document.querySelectorAll("#region button").forEach((x) => x.classList.toggle("on", x === b)); renderTape(); });
$("confirmed").addEventListener("change", (e) => { V.confirmed = e.target.checked; renderSignals(); });
$("direct").addEventListener("change", (e) => { V.direct = e.target.checked; renderTape(); });
$("q").addEventListener("input", (e) => { V.q = e.target.value; renderTape(); });
$("srcPop").addEventListener("change", (e) => { e.target.checked ? V.src.add(e.target.value) : V.src.delete(e.target.value); renderTape(); });
$("list").addEventListener("click", (e) => { const n = e.target.closest(".row"); if (n && n.dataset.k) openDrawer(n.dataset.k); });
$("list").addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.dataset.k) { e.preventDefault(); openDrawer(e.target.dataset.k); } });
$("feed").addEventListener("click", (e) => {
  const t = e.target.closest(".tag"), m = e.target.closest(".meaning");
  if (t) { (DATA.board || []).some((r) => r.ticker === t.dataset.t) ? openDrawer(t.dataset.t) : toast(t.dataset.t + " is not on the board yet"); }
  else if (m) m.classList.toggle("open");
});
$("scrim").addEventListener("click", closeDrawer); $("dClose").addEventListener("click", closeDrawer);
$("refresh").addEventListener("click", refresh); $("palBtn").addEventListener("click", openPal);
$("theme").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
pq.addEventListener("input", () => { palSel = 0; palRender(); });
pq.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { palSel = Math.min(palSel + 1, palItems.length - 1); palRender(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { palSel = Math.max(palSel - 1, 0); palRender(); e.preventDefault(); }
  else if (e.key === "Enter" && palItems[palSel]) { pal.close(); palItems[palSel].go(); }
});
pal.addEventListener("click", (e) => { if (e.target === pal) pal.close(); });
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (e.key === "Escape") closeDrawer();
  else if ((e.key === "/" && !typing) || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k")) { e.preventDefault(); openPal(); }
  else if (!typing && ["1", "2", "3"].includes(e.key)) setTab(["signals", "tape", "sources"][+e.key - 1]);
});

/* ---------- demo data (index.html?demo=1) ---------- */
function demoChart(a) {
  const close = []; let p = 100;
  for (let i = 0; i < 120; i++) { p *= 1 + (a === "SELL" ? -0.002 : 0.0025) + Math.sin(i / 7) * 0.006; close.push(+p.toFixed(2)); }
  const avg = (n, i) => (i < n - 1 ? null : +(close.slice(i - n + 1, i + 1).reduce((s, x) => s + x, 0) / n).toFixed(2));
  const last = close[119];
  return { close, sma20: close.map((_, i) => avg(20, i)), sma50: close.map((_, i) => avg(50, i)),
    levels: { support: +(last * 0.96).toFixed(2), resistance: +(last * 1.07).toFixed(2), stop: +(last * 0.94).toFixed(2), target: +(last * 1.07).toFixed(2) },
    entry_zone: [+(last * 0.96).toFixed(2), +(last * 1.01).toFixed(2)] };
}
function demoData() {
  const now = new Date().toISOString();
  const row = (ticker, action, net, conviction, why, x = {}) => ({ ticker, action, net, conviction, why, whys: [why], sources: ["Reuters", "Economic Times"], regions: ["Global"],
    headlines: ["Brent crude jumps 4% as Hormuz tensions flare", "RBI holds repo rate, keeps stance neutral"], direct_buy: action === "BUY" ? 2 : 0, direct_sell: action === "SELL" ? 2 : 0, indirect_buy: 0, indirect_sell: 0, ...x });
  const ch = (verdict, score, rr) => ({ verdict, score, rr, entry_zone: [412.5, 428.0], stop: 401.2, target: 462.0, reasons: ["trend up (+0.35)", "momentum RSI 58 / MACD (+0.25)", "history: 61% hit over 10d in 34 analogs (+0.22)"] });
  const tk = (t) => ({ ticker: t });
  return { count: 42, cycle: 7, updated_at: now, ai_ready: true, interpreter: "rules+ai",
    coverage: [{ source: "Economic Times", raw: 18 }, { source: "Moneycontrol", raw: 12 }, { source: "Reuters", raw: 9 }, { source: "Bloomberg", raw: 0 }],
    board: [row("ONGC", "BUY", 3.4, 4, "Higher crude lifts upstream realisations.", { chart: ch("CONFIRM", 0.62, 2.1) }),
      row("INDIGO", "SELL", -2.9, 4, "Jet fuel costs rise with crude; margins squeezed.", { chart: ch("CONTRADICT", -0.31, 1.1), news_action: "SELL" }),
      row("RELIANCE", "WATCH", 1.1, 2, "Refining gains offset by retail softness.", { news_action: "BUY", chart: ch("WAIT", 0.18, 1.2), priced_in: true }),
      row("HDFCBANK", "BUY", 2.2, 3, "Rate pause supports net interest margin outlook.", { chart: ch("NEUTRAL", 0.1, 1.7) }),
      row("TCS", "SELL", -1.8, 2, "Stronger rupee trims USD revenue translation.", { regions: ["India"] })],
    news: [{ id: "1", source: "Reuters", region: "Global", source_grade: "A", theme: "oil_up", interpreted_by: "ai", published_at: now, url: "https://example.com/1",
      title: "Brent crude jumps 4% as Hormuz tensions flare", meaning: "Higher oil raises costs for airlines and paints but lifts upstream producers' realisations.",
      direct_buy: [tk("ONGC")], direct_sell: [tk("INDIGO")], indirect_buy: [], indirect_sell: [tk("ASIANPAINT")] },
    { id: "2", source: "Economic Times", region: "India", source_grade: "B", theme: "rbi_pause", interpreted_by: "rules", published_at: now, url: "https://example.com/2",
      title: "RBI holds repo rate, keeps stance neutral", meaning: "A pause supports rate-sensitive sectors and bank margins.",
      direct_buy: [tk("HDFCBANK")], direct_sell: [], indirect_buy: [tk("HDFC")], indirect_sell: [] }] };
}

/* ---------- boot ---------- */
applyTheme(store.get("vm-theme", matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
setInterval(tick, 30000);
if (DEMO) { ingest(demoData()); setLive(true, "demo"); }
else {
  fetch("/api/briefing").then((r) => r.json()).then(ingest).catch(() => { $("skel").hidden = true; setLive(false, "offline"); });
  const es = new EventSource("/stream");
  es.onopen = () => setLive(true, "live"); es.onerror = () => setLive(false, "reconnecting");
  es.onmessage = (ev) => { try { ingest(JSON.parse(ev.data)); } catch { /* ignore parse blip */ } };
}
})();
