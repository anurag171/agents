const $ = (id) => document.getElementById(id);
let DATA = { news: [], board: [], buys: [], sells: [], watch: [], coverage: [] };
let FILTER = "all";
let QUERY = "";

const SOURCE_FILTERS = {
  et: "Economic Times",
  mc: "Moneycontrol",
  nse: "NSE",
  msn: "MSN Money",
  yf: "Yahoo Finance",
  mw: "MarketWatch",
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

function applyFilter(news) {
  const q = QUERY.trim().toLowerCase();
  return news.filter((n) => {
    if (FILTER === "india" && n.region !== "India") return false;
    if (FILTER === "global" && n.region !== "Global") return false;
    if (SOURCE_FILTERS[FILTER] && n.source !== SOURCE_FILTERS[FILTER]) return false;
    if (FILTER === "buy" && !(n.direct_buy.length || n.indirect_buy.length)) return false;
    if (FILTER === "sell" && !(n.direct_sell.length || n.indirect_sell.length)) return false;
    if (FILTER === "direct" && !(n.direct_buy.length || n.direct_sell.length)) return false;
    if (!q) return true;
    const blob = [
      n.title, n.theme, n.meaning, n.source, n.region,
      ...(n.signals || []).map((s) => s.ticker + " " + s.why),
    ].join(" ").toLowerCase();
    return blob.includes(q);
  });
}

function filterBoard(rows, action) {
  const q = QUERY.trim().toLowerCase();
  return rows.filter((r) => {
    if (action && r.action !== action) return false;
    if (FILTER === "direct" && r.direct_buy + r.direct_sell < 0.1) return false;
    if (!q) return true;
    return (r.ticker + " " + r.why + " " + r.headlines.join(" ")).toLowerCase().includes(q);
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
    el.innerHTML = '<li class="muted">No names in this bucket for the current tape.</li>';
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
    <div class="stat"><span>Live sources</span><b>${live}</b></div>
  `;
}

function render() {
  renderStats();
  renderBoard($("buys"), DATA.board || [], "BUY");
  renderBoard($("sells"), DATA.board || [], "SELL");
  renderBoard($("watch"), DATA.board || [], "WATCH");
  const news = applyFilter(DATA.news || []);
  if (!news.length) {
    $("feed").innerHTML = '<p class="muted">Waiting for mapped headlines from India + global sources...</p>';
    return;
  }
  $("feed").innerHTML = news
    .slice(0, 40)
    .map((n) => {
      const when = (n.published_at || n.fetched_at || "").replace("T", " ").slice(0, 16);
      return `<article class="card">
        <div>
          <div class="src">${n.region || ""} · ${n.source} · grade ${n.source_grade}</div>
          <div class="theme">${n.theme}</div>
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
  $("meta").textContent = `${n} decoded · cycle ${snap.cycle || 0} · ${t} UTC` + (live.length ? ` · ${live.slice(0, 6).join(", ")}` : "");
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
