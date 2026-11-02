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
const TRACK = { at: 0, h: 5, hz: 5, data: null };
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
const chip = (a, net, p) => {
  const c = mk("span", "chip " + a.toLowerCase(), `${GLYPH[a] || ""} ${a}`);
  if (a !== "WATCH" && p != null) c.append(mk("em", "", Math.round(num(p)) + "%"));
  else if (a !== "WATCH" && net != null) c.append(mk("em", "", (num(net) > 0 ? "+" : "") + num(net).toFixed(1)));
  return c;
};
const strengthOf = (r) => (r.score_pct == null ? -1 : num(r.score_pct));
const boardRows = () => (DATA.board || [])
  .filter((r) => (V.side === "ALL" || r.action === V.side) && (!V.confirmed || (r.chart && r.chart.verdict === "CONFIRM")))
  .sort((a, b) => strengthOf(b) - strengthOf(a) || num(b.conviction) - num(a.conviction) || Math.abs(num(b.net)) - Math.abs(num(a.net)));

function fillRow(n, r) {
  const a = r.action || "WATCH", conv = Math.max(0, Math.min(5, Math.round(num(r.conviction))));
  n.className = "row " + a.toLowerCase();
  const head = mk("div", "rh"); head.append(mk("b", "tk", r.ticker), chip(a, r.net, r.score_pct));
  const meter = mk("div", "meter"); meter.setAttribute("role", "img"); meter.setAttribute("aria-label", r.score_pct != null ? `Signal strength ${Math.round(num(r.score_pct))} percent, ${r.strength}` : `Conviction ${conv} of 5`);
  for (let i = 0; i < 5; i++) meter.append(mk("i", i < conv ? "on" : ""));
  const left = mk("div", "rl"); left.append(head, meter);
  const hop = num(r.direct_buy) + num(r.direct_sell) >= num(r.indirect_buy) + num(r.indirect_sell) ? "direct" : "indirect";
  const meta = mk("div", "meta");
  meta.append(mk("span", "", hop), mk("span", "", `${(r.headlines || []).length} prints`), mk("span", "", `${(r.sources || []).length} sources`));
  if (r.market) {
    const c = num(r.market.change_pct), cur = r.market.mkt === "US" ? "$" : "₹";
    meta.append(mk("span", "pct " + (c >= 0 ? "up" : "down"), `${cur}${num(r.market.price).toLocaleString()} ${c >= 0 ? "+" : ""}${c.toFixed(1)}%`));
  }
  const flags = mk("div", "flags");
  if (r.origin === "market-mover") flags.append(mk("span", "vchip v-note", "Market-driven · no news explains it yet"));
  ((r.market && r.market.flags) || []).forEach((f) => flags.append(mk("span", "vchip " + (f === "market disagrees" ? "v-contradict" : "v-note"), f)));
  if (r.chart && r.chart.verdict) flags.append(mk("span", "vchip v-" + r.chart.verdict.toLowerCase(), "Chart: " + r.chart.verdict.toLowerCase()));
  if (r.news_action && r.news_action !== a) flags.append(mk("span", "vchip v-note", "News said " + r.news_action));
  if (r.priced_in) flags.append(mk("span", "vchip v-wait", "Priced in"));
  if (a === "WATCH" && r.lean && r.score_pct != null) flags.append(mk("span", "vchip v-note", `Leaning ${r.lean} ${Math.round(num(r.score_pct))}%`));
  if (r.gated_by_strength) flags.append(mk("span", "vchip v-wait", "Below strength bar"));
  if (r.limited_data) flags.append(mk("span", "vchip v-note", "Limited data"));
  if (r.history && r.history.enough) flags.append(mk("span", "vchip v-confirm", `Past: ${Math.round(num(r.history.hit_rate) * 100)}% right (n=${num(r.history.n)})`));
  const mid = mk("div", "rm"); mid.append(mk("p", "why", r.why || "Aggregated from live headlines."), meta, flags);
  n.replaceChildren(left, mid, mk("span", "chev", "›"));
}
function renderSignals() {
  const rows = boardRows();
  sync($("list"), rows, (r) => r.ticker, () => { const d = mk("div"); d.tabIndex = 0; d.setAttribute("role", "button"); return d; }, fillRow);
  $("emptyS").hidden = rows.length > 0;
}

/* ---------- drawer ---------- */
function breakdown(r) {
  const wrap = mk("div");
  wrap.append(mk("p", "", r.score_pct == null ? "No news evidence behind this row; it is market-driven only." : `${Math.round(num(r.score_pct))}% · ${r.strength} · data coverage ${num(r.data_coverage_pct)}%` + (r.limited_data ? " (capped: limited data)" : "")));
  (r.signal_breakdown || []).forEach((b) => {
    const d = mk("div", "bk"); d.append(mk("span", "", `${b.label} · weight ${b.weight_pct}%`), mk("b", "", b.available ? b.value_pct + "%" : "no data"));
    const bar = mk("div", "sbar"); bar.style.setProperty("--w", (b.available ? num(b.value_pct) : 0) + "%"); bar.append(mk("i")); d.append(bar, mk("small", "meta", b.note || ""));
    wrap.append(d);
  });
  if (r.history && r.history.text) wrap.append(mk("p", "", r.history.text));
  wrap.append(mk("p", "meta", "Signal strength = how much agreeing evidence stands behind the call. It is not a probability of profit; the line above shows how calls like this have actually done so far."));
  return wrap;
}
const kv = (k, v) => { const d = mk("div", "kv"); d.append(mk("span", "", k), mk("b", "", v)); return d; };
const section = (title, ...kids) => { const s = mk("div", "sec"); s.append(mk("h4", "", title), ...kids); return s; };
function openDrawer(ticker) {
  const r = (DATA.board || []).find((x) => x.ticker === ticker);
  if (!r) return;
  lastFocus = document.activeElement;
  $("dTitle").textContent = r.ticker;
  const body = $("dBody"), a = r.action || "WATCH", urls = new Map((DATA.news || []).map((n) => [n.title, n.url]));
  const top = mk("div", "rh"); top.append(chip(a, r.net, r.score_pct), mk("span", "meta", r.score_pct != null ? r.strength : "No news signal"));
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
  const parts = [top, section("How strong is this signal?", breakdown(r)), section("Why", why), section("Evidence", ev.children.length ? ev : mk("p", "meta", "No linked headlines.")), section("Chart & price range", chartBox)];
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
  if (num(it.n_sources) > 1) { const b = mk("span", "badge ai", `+${num(it.n_sources) - 1} outlets`); b.title = "Also reported by: " + (it.sources || []).join(", "); t.append(b); }
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

/* ---------- market movers ---------- */
const PANELS = [["gainers", "Top gainers"], ["losers", "Top losers"], ["active", "Most active"], ["near_high", "Near 52-week high"], ["near_low", "Near 52-week low"], ["volume_shock", "Volume shockers"]];
function renderMarket() {
  const mv = DATA.movers || {}, onBoard = new Set((DATA.board || []).map((r) => r.ticker));
  const live = (DATA.coverage || []).filter((c) => /^Market:/.test(c.source));
  $("mktNote").textContent = live.length ? live.map((c) => `${c.source.replace("Market: ", "")}: ${num(c.raw)} quotes`).join(" · ") : "No market-data source configured.";
  const cols = PANELS.filter(([k]) => (mv[k] || []).length).map(([k, title]) => {
    const p = mk("div", "pan"); p.append(mk("h3", "", title));
    (mv[k] || []).slice(0, 8).forEach((q) => {
      const b = mk("button", "q"); b.type = "button"; b.dataset.t = q.symbol;
      const nm = mk("span"); nm.append(mk("b", "tk", q.symbol), mk("small", "", (onBoard.has(q.symbol) ? "● on board · " : "") + (q.name || "")));
      const c = num(q.change_pct);
      b.append(nm, mk("span", "", (q.mkt === "US" ? "$" : "₹") + num(q.price).toLocaleString()), mk("span", "pct " + (c >= 0 ? "up" : "down"), `${c >= 0 ? "+" : ""}${c.toFixed(1)}%`));
      p.append(b);
    });
    return p;
  });
  $("mktCols").replaceChildren(...(cols.length ? cols : [mk("p", "empty", "No live quotes yet. Configure NSE/Yahoo access or drop CSVs in VM_QUOTES_DIR.")]));
}

/* ---------- assets ---------- */
const fmt = (v) => num(v).toLocaleString(undefined, { maximumFractionDigits: num(v) < 10 ? 3 : 2 });
const signed = (v, d = 1) => (v == null ? "–" : `${num(v) >= 0 ? "+" : ""}${num(v).toFixed(d)}%`);
function renderAssets() {
  const groups = {};
  (DATA.assets || []).forEach((a) => (groups[a.cls] = groups[a.cls] || []).push(a));
  const cols = Object.keys(groups).map((cls) => {
    const p = mk("div", "pan"); p.append(mk("h3", "", cls));
    groups[cls].forEach((a) => {
      const row = mk("div", "arow"), name = mk("span");
      name.append(mk("b", "", a.label), mk("small", "", a.available ? `${a.unit} · 5d ${signed(a.chg_5d)} · 20d ${signed(a.chg_20d)}` : "no live data"));
      const sp = mk("span", "spark " + (a.trend || "")); if (a.available && window.renderChart) window.renderChart(sp, { close: a.spark }, 96, 30);
      const px = mk("span"); px.style.textAlign = "right";
      if (a.available) { px.append(mk("b", "", fmt(a.price)), mk("small", "pct " + (num(a.change_pct) >= 0 ? "up" : "down"), signed(a.change_pct))); }
      row.append(name, sp, px);
      const fl = mk("div", "flags ev");
      if (a.action === "BUY" || a.action === "SELL") fl.append(chip(a.action, null, a.score_pct));
      if (a.news_action) fl.append(mk("span", "vchip v-note", "News said " + a.news_action));
      if (a.chart && a.chart.verdict) fl.append(mk("span", "vchip v-" + a.chart.verdict.toLowerCase(), "Chart: " + a.chart.verdict.toLowerCase()));
      if (a.note) fl.append(mk("span", "", a.note));
      const e = (a.evidence || [])[0]; if (e) fl.append(mk("span", "", `${e.theme}: ${e.why}`));
      row.append(fl); p.append(row);
    });
    return p;
  });
  $("assetCols").replaceChildren(...(cols.length ? cols : [mk("p", "empty", "No asset data yet.")]));
}

/* ---------- IPOs ---------- */
const VCLS = { FAVOURABLE: "v-confirm", CAUTION: "v-contradict", NEUTRAL: "v-neutral", "INSUFFICIENT DATA": "v-unavailable" };
function scoreBar(label, v) { const d = mk("div"); d.append(mk("span", "", label), mk("b", "", Math.round(num(v)))); const b = mk("div", "sbar"); b.style.setProperty("--w", Math.max(0, Math.min(100, num(v))) + "%"); b.append(mk("i")); d.append(b); return d; }
function fillIpo(n, d) {
  n.className = "card";
  const t = d.terms || {}, top = mk("div", "top2");
  top.append(mk("span", "chip " + (d.status === "open" ? "buy" : "watch"), (d.status || "").toUpperCase()),
    mk("span", "", [t.open && "opens " + t.open, t.close && "closes " + t.close].filter(Boolean).join(" · ")),
    mk("span", "", d.price_band ? `band ₹${d.price_band[0]}–${d.price_band[1]}` : ""), mk("span", "", t.min_investment ? `min ₹${num(t.min_investment).toLocaleString()}` : ""));
  const h = mk("h3"); if (d.url) { const l = mk("a", "", d.name); l.href = safeUrl(d.url); l.target = "_blank"; l.rel = "noopener noreferrer"; h.append(l); } else h.textContent = d.name;
  const v = mk("div", "flags"); v.append(mk("span", "vchip " + (VCLS[d.verdict] || "v-note"), d.verdict), mk("span", "", `data coverage ${Math.round(num(d.coverage) * 100)}%`), mk("span", "", "sources: " + (d.sources || []).join(", ")));
  const parts = [top, h, v];
  if (d.verdict === "INSUFFICIENT DATA") parts.push(mk("p", "warn", "Not enough fundamentals to score. Add valuation, profitability and use-of-proceeds data via VM_IPO_DIR."));
  else { const sc = mk("div", "scores"); sc.append(scoreBar("Listing-day view", d.listing_score), scoreBar("Long-term view", d.longterm_score)); parts.push(sc); }
  const sub = d.subscription || {}; if (sub.total != null) parts.push(mk("p", "meta", `Subscribed ${num(sub.total)}x overall` + (sub.qib != null ? ` · QIB ${num(sub.qib)}x` : "")));
  if (d.gmp_pct != null) parts.push(mk("p", "meta", `Grey-market premium ${signed(d.gmp_pct)} (unofficial, not scored)`));
  (d.flags || []).filter((f) => !/^GMP/.test(f)).forEach((f) => parts.push(mk("p", "warn", "⚠ " + f)));
  const all = [...((d.factors || {}).listing || []).map((f) => ["Listing", f]), ...((d.factors || {}).longterm || []).map((f) => ["Long-term", f])];
  if (all.length) { const det = mk("details"); det.append(mk("summary", "", "Why these scores")); const ul = mk("ul"); all.forEach(([k, f]) => ul.append(mk("li", "", `${k} · ${f.factor} (${f.points >= 0 ? "+" : ""}${f.points}): ${f.why}`))); det.append(ul); parts.push(det); }
  if (d.listing_gain_pct != null) parts.push(mk("p", "meta", `Listing gain vs issue price: ${signed(d.listing_gain_pct)}`));
  parts.push(mk("p", "meta", d.disclaimer || "Educational summary, not advice."));
  n.replaceChildren(...parts);
}
function renderIpos() {
  const items = DATA.ipos || [], open = items.filter((i) => i.status === "open").length;
  $("ipoNote").textContent = items.length ? `${items.length} issues · ${open} open now. Scores are rule-based and show their working; grey-market premium is never scored.` : "";
  sync($("ipoList"), items, (d) => d.name, () => mk("article"), fillIpo);
  $("emptyI").hidden = items.length > 0;
}

/* ---------- track record ---------- */
const pc = (v, d = 0) => (v == null ? "–" : (num(v) * 100).toFixed(d) + "%");
const sg = (v) => (v == null ? "–" : `${num(v) >= 0 ? "+" : ""}${num(v).toFixed(2)}%`);
function statRow(label, st, minN) {
  const tr = mk("tr", st && st.n && !st.enough ? "dim" : ""), n = st ? num(st.n) : 0;
  const hit = mk("td"); if (n) { hit.append(mk("b", "", pc(st.hit_rate)), mk("small", "", ` (${pc(st.hit_ci[0])}–${pc(st.hit_ci[1])})`)); const b = mk("div", "sbar"); b.style.setProperty("--w", num(st.hit_rate) * 100 + "%"); b.append(mk("i")); hit.append(b); } else hit.textContent = "–";
  tr.append(mk("td", "", label), mk("td", "", n ? (st.enough ? String(n) : `${n} (need ${minN})`) : "0"), hit, mk("td", "", n && st.excess_hit_rate != null ? pc(st.excess_hit_rate) : "–"), mk("td", "", n ? sg(st.mean_ret) : "–"), mk("td", "", n && st.mean_excess != null ? sg(st.mean_excess) : "–"));
  return tr;
}
function statTable(title, groups, minN) {
  const wrap = mk("div"); wrap.append(mk("h4", "cap", title));
  const t = mk("table", "tbl"), hd = mk("thead"), hr = mk("tr");
  ["Group", "Calls", "Went the called way (95% range)", "Beat the index", "Avg return", "Avg vs index"].forEach((h) => hr.append(mk("th", "", h)));
  hd.append(hr); const tb = mk("tbody");
  Object.entries(groups).forEach(([k, st]) => tb.append(statRow(k, st, minN))); t.append(hd, tb); wrap.append(t); return wrap;
}
function renderTrackData(d) {
  const o = d.overall || { n: 0 }, parts = [];
  $("trackNote").textContent = `${d.logged} calls logged · ${d.settled} settled at ${d.horizon} days · ${d.pending} still waiting`;
  if (!o.n) parts.push(mk("p", "big", "No settled calls yet."), mk("p", "meta", "Every call is logged when it is made and checked against the price 1, 5 and 20 trading days later. The first results appear after the first horizon passes; a percentage is only shown once 30 calls have settled."));
  else {
    parts.push(mk("p", "big", `Of ${o.n} settled calls, ${pc(o.hit_rate)} went the called way over ${d.horizon} trading days (95% range ${pc(o.hit_ci[0])}–${pc(o.hit_ci[1])}).`));
    if (d.benchmark_up_rate != null) parts.push(mk("p", "meta", `For context, the index itself rose in ${pc(d.benchmark_up_rate)} of those windows, so a high hit rate in a rising market is partly luck. Compare the “beat the index” column.`));
    parts.push(mk("p", "", d.predictive.verdict));
  }
  const buckets = {}; Object.entries(d.by_bucket || {}).forEach(([k, v]) => { if (v.n) buckets[k] = v; });
  if (Object.keys(buckets).length) parts.push(statTable("By signal strength — does a higher % really do better?", buckets, d.min_n));
  if (Object.keys(d.by_chart || {}).length) parts.push(statTable("By chart check — does it help?", d.by_chart, d.min_n));
  const shown = {}; Object.entries(d.by_shown || {}).forEach(([k, v]) => { if (v.n) shown[k] = v; });
  if (Object.keys(shown).length) parts.push(statTable("Calls shown vs calls held back — do the filters help?", shown, d.min_n));
  const ul = mk("ul", "meta"); (d.notes || []).forEach((x) => ul.append(mk("li", "", x))); parts.push(ul);
  $("trackBody").replaceChildren(...parts);
}
function renderTrack() {
  if (TRACK.data && TRACK.h === TRACK.hz && Date.now() - TRACK.at < 30000) return renderTrackData(TRACK.data);
  const h = TRACK.hz;
  (DEMO ? Promise.resolve(demoPerf(h)) : fetch(`/api/performance?horizon=${h}`).then((r) => (r.ok ? r.json() : Promise.reject())))
    .then((d) => { TRACK.data = d; TRACK.h = h; TRACK.at = Date.now(); if (V.tab === "track") renderTrackData(d); })
    .catch(() => $("trackBody").replaceChildren(mk("p", "empty", "Track record unavailable.")));
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
function renderAll() { renderKpis(); renderSignals(); if (V.tab === "market") renderMarket(); if (V.tab === "assets") renderAssets(); if (V.tab === "ipos") renderIpos(); if (V.tab === "track") renderTrack(); if (V.tab === "tape") renderTape(); if (V.tab === "sources") renderSources(); }
function ingest(snap) {
  if (!snap) return;
  DATA = snap; $("skel").hidden = true; renderAll(); tick();
}
function setTab(t) {
  V.tab = t;
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
  ["signals", "market", "assets", "ipos", "track", "tape", "sources"].forEach((v) => ($("v-" + v).hidden = v !== t));
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
$("mktCols").addEventListener("click", (e) => { const b = e.target.closest(".q"); if (!b) return; (DATA.board || []).some((r) => r.ticker === b.dataset.t) ? openDrawer(b.dataset.t) : toast(b.dataset.t + ": market-driven, no news signal yet"); });
$("hz").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; TRACK.hz = +b.dataset.h; document.querySelectorAll("#hz button").forEach((x) => x.classList.toggle("on", x === b)); renderTrack(); });
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
  else if (!typing && ["1", "2", "3", "4", "5", "6", "7"].includes(e.key)) setTab(["signals", "market", "assets", "ipos", "track", "tape", "sources"][+e.key - 1]);
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
function demoPerf(h) {
  const st = (n, hit, ret, ex) => ({ n, enough: n >= 30, hit_rate: hit, hit_ci: [Math.max(0, hit - 0.1), Math.min(1, hit + 0.1)], mean_ret: ret, median_ret: ret, excess_hit_rate: hit - 0.04, mean_excess: ex, n_excess: n });
  return { horizon: h, min_n: 30, logged: 214, settled: 196, pending: 18, overall: st(196, 0.58, 0.9, 0.4), benchmark_up_rate: 0.54,
    by_bucket: { "0-39%": { n: 0 }, "40-49%": st(22, 0.45, -0.3, -0.5), "50-59%": st(41, 0.51, 0.1, -0.1), "60-69%": st(48, 0.56, 0.6, 0.2), "70-79%": st(44, 0.61, 1.1, 0.6), "80-89%": st(34, 0.65, 1.6, 0.9), "90-100%": st(7, 0.71, 2.0, 1.1) },
    by_chart: { CONFIRM: st(70, 0.64, 1.4, 0.8), NEUTRAL: st(80, 0.55, 0.6, 0.2), WAIT: st(26, 0.5, 0.1, -0.2), CONTRADICT: st(20, 0.4, -0.8, -1.0) },
    by_shown: { "shown as BUY/SELL": st(118, 0.62, 1.2, 0.6), "held back to WATCH": st(78, 0.5, 0.2, -0.1) },
    predictive: { n: 196, ic: 0.18, p_value: 0.011, predictive: true, verdict: "Higher strength has gone with better outcomes so far (rank correlation +0.18, p=0.011, n=196)." },
    notes: ["Demo data - not real results.", "'Right' = price moved in the called direction; returns exclude costs, slippage and taxes."] };
}
function demoData() {
  const sp = (a, b) => Array.from({ length: 40 }, (_, i) => a + ((b - a) * i) / 39 + Math.sin(i / 3) * (b - a) * 0.05);
  const now = new Date().toISOString();
  const bd = (c) => { const p = Math.min(95, c * 18 + 8); return { score_pct: p, strength: p >= 80 ? "Very strong" : p >= 60 ? "Strong" : p >= 40 ? "Moderate" : "Weak", data_coverage_pct: 100, lean: null, calibrated: false,
    signal_breakdown: [["News strength", 30, 88, "net news score +3.4"], ["Evidence agrees", 10, 100, "3.4 supporting vs 0.0 opposing"], ["Independent confirmation", 15, 65, "2 event(s) from 3 outlet(s)"], ["Source quality", 10, 100, "best source grade A"], ["Chart check", 25, 82, "confirm: trend up (+0.35)"], ["Market reaction", 10, 80, "+3.6% today - price is moving the same way"]]
      .map(([label, w, v, note]) => ({ label, weight_pct: w, value_pct: v, available: true, note })) }; };
  const row = (ticker, action, net, conviction, why, x = {}) => ({ ticker, action, net, conviction, why, whys: [why], sources: ["Reuters", "Economic Times"], regions: ["Global"],
    headlines: ["Brent crude jumps 4% as Hormuz tensions flare", "RBI holds repo rate, keeps stance neutral"], direct_buy: action === "BUY" ? 2 : 0, direct_sell: action === "SELL" ? 2 : 0, indirect_buy: 0, indirect_sell: 0, ...(x.score_pct === undefined && conviction > 1 ? bd(conviction) : {}), ...x });
  const ch = (verdict, score, rr) => ({ verdict, score, rr, entry_zone: [412.5, 428.0], stop: 401.2, target: 462.0, reasons: ["trend up (+0.35)", "momentum RSI 58 / MACD (+0.25)", "history: 61% hit over 10d in 34 analogs (+0.22)"] });
  const tk = (t) => ({ ticker: t });
  return { count: 42, cycle: 7, updated_at: now, ai_ready: true, interpreter: "rules+ai",
    movers: { gainers: [{ symbol: "ONGC", name: "Oil & Natural Gas Corp", mkt: "IN", price: 268.4, change_pct: 3.6 }, { symbol: "ZOMATO", name: "Zomato Ltd", mkt: "IN", price: 255.1, change_pct: 5.2 }],
        losers: [{ symbol: "INDIGO", name: "InterGlobe Aviation", mkt: "IN", price: 4310, change_pct: -2.8 }], active: [{ symbol: "NVDA", name: "NVIDIA", mkt: "US", price: 905.2, change_pct: 2.1 }] },
      assets: [
        { key: "gold", label: "Gold", cls: "Commodity", unit: "USD/oz", available: true, price: 2650.4, change_pct: 0.8, chg_5d: 2.1, chg_20d: 4.5, trend: "up", spark: sp(2480, 2650), action: "BUY", news_score: 2.6, evidence: [{ theme: "Gold bid", effect: "BUY", why: "Safe-haven demand", items: 2 }], chart: { verdict: "CONFIRM" } },
        { key: "brent", label: "Brent crude", cls: "Commodity", unit: "USD/bbl", available: true, price: 88.2, change_pct: 2.6, chg_5d: 5.0, chg_20d: 9.1, trend: "up", spark: sp(78, 88), action: "WATCH", news_score: 1.0, evidence: [] },
        { key: "us10y", label: "US 10Y yield", cls: "Rates", unit: "%", note: "Rising yield = falling bond prices", available: true, price: 4.31, change_pct: -0.7, chg_5d: -1.2, chg_20d: 2.0, trend: "down", spark: sp(4.4, 4.31), action: "WATCH", news_score: 0, evidence: [] },
        { key: "btc", label: "Bitcoin", cls: "Crypto", unit: "USD", available: false, action: "WATCH", news_score: 0, evidence: [] }],
      ipos: [
        { name: "Acme Fasteners", status: "open", verdict: "FAVOURABLE", listing_score: 74, longterm_score: 68, coverage: 0.8, sources: ["file", "nse"], price_band: [100, 105], url: "https://example.com/acme",
          terms: { open: "20-Oct-2026", close: "23-Oct-2026", min_investment: 14910 }, subscription: { total: 12.5, qib: 18 }, gmp_pct: 19, flags: ["GMP +19% is unofficial and can be manipulated; not scored"],
          factors: { listing: [{ factor: "demand", points: 12, why: "subscribed 12.5x overall" }], longterm: [{ factor: "valuation", points: 18, why: "P/E 18 vs peers 25" }] }, disclaimer: "Educational summary, not advice." },
        { name: "Zed Tech", status: "upcoming", verdict: "INSUFFICIENT DATA", listing_score: 50, longterm_score: 50, coverage: 0.1, sources: ["news"], price_band: [50, 52], terms: {}, subscription: {}, flags: [], factors: { listing: [], longterm: [] } }],
      coverage: [{ source: "Market: NSE", raw: 497 }, { source: "Economic Times", raw: 18 }, { source: "Moneycontrol", raw: 12 }, { source: "Reuters", raw: 9 }, { source: "Bloomberg", raw: 0 }],
    board: [row("ONGC", "BUY", 3.4, 4, "Higher crude lifts upstream realisations.", { history: { enough: true, n: 34, hit_rate: 0.65, text: "Calls at this strength went the called way 65% of the time over 5 trading days (n=34, 95% range 48%-79%); average +1.6%." }, chart: ch("CONFIRM", 0.62, 2.1), market: { price: 268.4, change_pct: 3.6, mkt: "IN", flags: [] } }),
      row("ZOMATO", "WATCH", 0, 1, "Top gainer: +5.2% — no mapped news explains the move yet.", { origin: "market-mover", score_pct: null, strength: "No news signal", market: { price: 255.1, change_pct: 5.2, mkt: "IN", flags: ["volume 2.4x avg"] } }),
      row("INDIGO", "SELL", -2.9, 4, "Jet fuel costs rise with crude; margins squeezed.", { chart: ch("CONTRADICT", -0.31, 1.1), news_action: "SELL" }),
      row("RELIANCE", "WATCH", 1.1, 2, "Refining gains offset by retail softness.", { news_action: "BUY", chart: ch("WAIT", 0.18, 1.2), priced_in: true }),
      row("HDFCBANK", "BUY", 2.2, 3, "Rate pause supports net interest margin outlook.", { chart: ch("NEUTRAL", 0.1, 1.7) }),
      row("TCS", "SELL", -1.8, 2, "Stronger rupee trims USD revenue translation.", { regions: ["India"] })],
    news: [{ id: "1", source: "Reuters", region: "Global", source_grade: "A", theme: "oil_up", interpreted_by: "ai", published_at: now, url: "https://example.com/1",
      title: "Brent crude jumps 4% as Hormuz tensions flare", meaning: "Higher oil raises costs for airlines and paints but lifts upstream producers' realisations.",
      n_sources: 3, sources: ["Reuters", "Moneycontrol", "Economic Times"], direct_buy: [tk("ONGC")], direct_sell: [tk("INDIGO")], indirect_buy: [], indirect_sell: [tk("ASIANPAINT")] },
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
