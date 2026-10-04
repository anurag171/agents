const $ = (id) => document.getElementById(id);
let DATA = { news: [], board: [], buys: [], sells: [], watch: [], coverage: [] };
let FILTER = "all";
let QUERY = "";

const SOURCE_FILTERS = {
  et: "Economic Times",
  mc: "Moneycontrol",
  nse: "NSE",
  msn: "MSN Money",
  cnbc: "CNBC-TV18",
  bs: "Business Standard",
  yf: "Yahoo Finance",
  mw: "MarketWatch",
  inv: "Investing.com",
  sa: "Seeking Alpha",
  bbg: "Bloomberg",
  reu: "Reuters",
  ft: "Financial Times",
  wsj: "WSJ",
};

const FILTER_LABELS = {
  all: "all tape",
  india: "India sources",
  global: "global sources",
  buy: "BUY-mapped headlines",
  sell: "SELL-mapped headlines",
  direct: "direct cash-flow names",
};

function tick() {
  const now = new Date();
  $("clock").textContent =
    now.toISOString().replace("T", " ").slice(0, 19) + " UTC  ·  " +
    now.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").textContent = theme === "dark" ? "Light" : "Dark";
  localStorage.setItem("vm-theme", theme);
}

function sourceName() {
  return SOURCE_FILTERS[FILTER] || "";
}

function newsMatchesFilter(n) {
  const src = sourceName();
  if (FILTER === "india" && n.region !== "India") return false;
  if (FILTER === "global" && n.region !== "Global") return false;
  if (src && n.source !== src) return false;
  if (FILTER === "buy" && !(n.direct_buy.length || n.indirect_buy.length)) return false;
  if (FILTER === "sell" && !(n.direct_sell.length || n.indirect_sell.length)) return false;
  if (FILTER === "direct" && !(n.direct_buy.length || n.direct_sell.length)) return false;
  return true;
}

function boardMatchesFilter(r) {
  const src = sourceName();
  if (FILTER === "india" && !(r.regions || []).includes("India")) return false;
  if (FILTER === "global" && !(r.regions || []).includes("Global")) return false;
  if (src && !(r.sources || []).includes(src)) return false;
  if (FILTER === "direct" && r.direct_buy + r.direct_sell < 0.1) return false;
  return true;
}

function queryHit(blob) {
  const q = QUERY.trim().toLowerCase();
  if (!q) return true;
  return blob.toLowerCase().includes(q);
}

function applyFilter(news) {
  return news.filter((n) => {
    if (!newsMatchesFilter(n)) return false;
    const blob = [
      n.title, n.theme, n.meaning, n.source, n.region, n.ai_meaning,
      ...(n.signals || []).map((s) => s.ticker + " " + s.why),
    ].join(" ");
    return queryHit(blob);
  });
}

function filterBoard(rows, action) {
  return rows.filter((r) => {
    if (action && r.action !== action) return false;
    if (FILTER === "buy" && action !== "BUY") return false;
    if (FILTER === "sell" && action !== "SELL") return false;
    if (!boardMatchesFilter(r)) return false;
    return queryHit(r.ticker + " " + r.why + " " + (r.headlines || []).join(" "));
  });
}

function tags(list, cls) {
  if (!list.length) return '<span class="muted">none mapped</span>';
  return list
    .slice(0, 8)
    .map((s) => `<span class="tag ${cls}">${s.ticker}</span>`)
    .join("");
}

function renderBoard(el, rows, action) {
  const data = filterBoard(rows, action);
  if (!data.length) {
    el.innerHTML = '<li class="muted">No names in this bucket for the current filter.</li>';
    return;
  }
  el.innerHTML = data
    .slice(0, 12)
    .map((r) => {
      const hop = r.direct_buy + r.direct_sell >= r.indirect_buy + r.indirect_sell ? "direct" : "indirect";
      return `<li class="row">
        <div class="ticker">${r.ticker}</div>
        <div class="why">${r.why || "Aggregated from live headlines."}<div class="muted">${hop} · conv ${r.conviction}/5 · ${r.headlines.length} prints</div></div>
        <div class="chip ${r.action.toLowerCase()}">${r.action} ${r.net > 0 ? "+" : ""}${r.net}</div>
      </li>`;
    })
    .join("");
}

function renderStats() {
  const n = DATA.count || (DATA.news || []).length;
  const buys = (DATA.buys || []).length;
  const sells = (DATA.sells || []).length;
  const live = (DATA.coverage || []).filter((c) => c.raw > 0).length;
  $("stats").innerHTML = `
    <div class="stat"><span>Decoded</span><b>${n}</b></div>
    <div class="stat"><span>Buy names</span><b>${buys}</b></div>
    <div class="stat"><span>Sell names</span><b>${sells}</b></div>
    <div class="stat"><span>${DATA.ai_ready ? "Interpreter" : "Live sources"}</span><b>${DATA.ai_ready ? (DATA.interpreter || "ai") : live}</b></div>
  `;
}

function renderHint(newsCount) {
  const label = SOURCE_FILTERS[FILTER] || FILTER_LABELS[FILTER] || FILTER;
  const q = QUERY.trim();
  $("filterHint").textContent =
    `Showing ${newsCount} headlines · filter: ${label}` + (q ? ` · search "${q}"` : "");
}

function render() {
  renderStats();
  renderBoard($("buys"), DATA.board || [], "BUY");
  renderBoard($("sells"), DATA.board || [], "SELL");
  renderBoard($("watch"), DATA.board || [], "WATCH");
  const news = applyFilter(DATA.news || []);
  renderHint(news.length);
  if (!news.length) {
    $("feed").innerHTML = '<p class="muted">No headlines match this filter. Try All tape, or wait for the next scrape.</p>';
    return;
  }
  $("feed").innerHTML = news
    .slice(0, 40)
    .map((n) => {
      const when = (n.published_at || n.fetched_at || "").replace("T", " ").slice(0, 16);
      const by = n.interpreted_by === "ai" ? "AI" : "rules";
      return `<article class="card">
        <div>
          <div class="src">${n.region || ""} · ${n.source} · grade ${n.source_grade}</div>
          <div class="theme">${n.theme}</div>
          <span class="badge ${n.interpreted_by === "ai" ? "ai" : ""}">${by}</span>
          <div class="muted">${when} UTC<br/>${n.tape}</div>
        </div>
        <div>
          <h3><a href="${n.url}" target="_blank" rel="noopener">${n.title}</a></h3>
          <p class="meaning"><strong>Meaning:</strong> ${n.meaning}</p>
          <div class="legs">
            <div class="leg"><strong>DIRECT BUY</strong>${tags(n.direct_buy, "buy")}</div>
            <div class="leg"><strong>DIRECT SELL</strong>${tags(n.direct_sell, "sell")}</div>
            <div class="leg"><strong>INDIRECT BUY</strong>${tags(n.indirect_buy, "buy")}</div>
            <div class="leg"><strong>INDIRECT SELL</strong>${tags(n.indirect_sell, "sell")}</div>
          </div>
        </div>
      </article>`;
    })
    .join("");
}

function ingest(snap) {
  DATA = snap || DATA;
  const n = (snap && snap.count) || 0;
  const t = (snap && snap.updated_at) ? snap.updated_at.replace("T", " ").slice(0, 19) : "pending";
  const live = ((snap && snap.coverage) || []).filter((c) => c.raw > 0).map((c) => c.source);
  const interp = snap && snap.ai_ready ? ` · ${snap.interpreter || "ai"}` : " · rules (set USER_LLM_API_KEY)";
  $("meta").textContent = `${n} decoded · cycle ${snap.cycle || 0} · ${t} UTC${interp}` + (live.length ? ` · ${live.slice(0, 5).join(", ")}` : "");
  $("pulse").classList.toggle("live", n > 0);
  render();
}

async function refresh() {
  $("meta").textContent = "scraping India + global tape...";
  try {
    const r = await fetch("/api/refresh");
    ingest(await r.json());
  } catch (e) {
    $("meta").textContent = "scrape failed — retrying stream";
  }
}

$("refresh").addEventListener("click", refresh);
$("q").addEventListener("input", (e) => { QUERY = e.target.value; render(); });
document.querySelectorAll(".pills button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".pills button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    FILTER = b.dataset.f;
    render();
  });
});
$("theme").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

const saved = localStorage.getItem("vm-theme");
applyTheme(saved === "light" ? "light" : "dark");

setInterval(tick, 1000);
tick();

fetch("/api/briefing").then((r) => r.json()).then(ingest).catch(() => {});

const es = new EventSource("/stream");
es.onmessage = (ev) => {
  try { ingest(JSON.parse(ev.data)); } catch (e) { /* ignore parse blip */ }
};
es.onerror = () => { $("pulse").classList.remove("live"); };
