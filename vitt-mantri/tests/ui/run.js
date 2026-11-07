// Browser-behaviour checks for the UI, run in jsdom (a simulated browser, NOT a real one).
// Usage:  (cd tests/ui && npm i jsdom) ; (cd static-site-dir && python3 -m http.server 8765 --bind 127.0.0.1 &) ; node tests/ui/run.js
// where the served directory contains index.html (copy of templates/index.html) and static/ (copy of static/). Demo mode (?demo=1) supplies the data.
const { JSDOM, VirtualConsole } = require("jsdom");
const errors = [];
const vc = new VirtualConsole(); vc.on("jsdomError", (e) => errors.push("jsdomError: " + (e.detail || e.message))); vc.on("error", (e) => errors.push("console.error: " + e));
const pre = (w) => { w.matchMedia = () => ({ matches: false }); };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const ok = (name, cond) => { results.push((cond ? "PASS " : "FAIL ") + name); };
(async () => {
  // 1) demo mode
  let dom = await JSDOM.fromURL("http://127.0.0.1:8765/index.html?demo=1", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc, beforeParse: pre });
  await wait(600); let d = dom.window.document;
  ok("signals rendered (6 rows)", d.querySelectorAll("#list .row").length === 6);
  ok("row shows demoted-theme flag", [...d.querySelectorAll("#list .vchip")].some((c) => /Theme demoted/.test(c.textContent)));
  ok("row shows price and % change", !!d.querySelector("#list .row .meta .pct.up"));
  ok("market-driven row labelled", [...d.querySelectorAll("#list .vchip")].some((c) => /Market-driven/.test(c.textContent)));
  ok("KPI strip rendered (6)", d.querySelectorAll("#kpis .kpi").length === 6);
  ok("skeleton hidden", d.getElementById("skel").hidden === true);
  ok("chart verdict chip present", !!d.querySelector(".vchip.v-confirm"));
  ok("rows show percentage chip", [...d.querySelectorAll("#list .row .chip.buy em, #list .row .chip.sell em")].length >= 3 && [...d.querySelectorAll("#list .row .chip em")].every((e) => /^\d+%$/.test(e.textContent)));
  ok("rows sorted by strength desc", (() => { const v = [...d.querySelectorAll("#list .row")].map((r) => { const m = r.querySelector(".chip em"); return m ? parseInt(m.textContent) : -1; }); return v.every((x, i) => i === 0 || v[i - 1] >= x); })());
  ok("market-driven row shows no fake percentage", (() => { const z = [...d.querySelectorAll("#list .row")].find((r) => /ZOMATO/.test(r.textContent)); return !!z && !z.querySelector(".chip em"); })());
  d.querySelector('#list .row').click(); await wait(200);
  ok("drawer shows observed track record line", /went the called way 65%.*23 days/.test(d.getElementById("dBody").textContent));
  ok("row shows past hit-rate chip", [...d.querySelectorAll("#list .vchip")].some((c) => /Past: 65% right \(n=34\)/.test(c.textContent)));
  ok("drawer explains strength (6 components)", d.querySelectorAll("#dBody .bk").length === 6 && /not a probability of profit/.test(d.getElementById("dBody").textContent) && /Chart check/.test(d.getElementById("dBody").textContent));
  ok("drawer opens", d.getElementById("drawer").hidden === false);
  ok("chart svg drawn in drawer", !!d.querySelector("#chartBox svg path"));
  ok("main inert while drawer open", d.getElementById("main").inert === true);
  d.getElementById("dClose").click(); ok("drawer closes", d.getElementById("drawer").hidden === true);
  d.querySelector('#side [data-s="SELL"]').click(); ok("SELL filter", [...d.querySelectorAll("#list .row")].every((r) => r.classList.contains("sell")) && d.querySelectorAll("#list .row").length === 2);
  d.querySelector('[data-tab="tape"]').click(); ok("tape tab renders cards", d.querySelectorAll("#feed .card").length === 2);
  ok("tape: corroboration badge shows +2 outlets with names", [...d.querySelectorAll("#feed .badge")].some((b) => b.textContent === "+2 outlets" && /Reuters/.test(b.title)));
  d.getElementById("q").value = "rbi"; d.getElementById("q").dispatchEvent(new dom.window.Event("input")); 
  ok("tape: demoted theme badge on the RBI card", [...d.querySelectorAll("#feed .badge")].some((b) => b.textContent === "theme demoted"));
  ok("tape search filters", d.querySelectorAll("#feed .card").length === 1);
  d.querySelector('[data-tab="sources"]').click(); ok("sources table rows (incl. market feed)", d.querySelectorAll("#srcBody tr").length === 5);
  d.getElementById("confirmed"); d.querySelector('[data-tab="signals"]').click(); d.querySelector('#side [data-s="ALL"]').click();
  d.getElementById("confirmed").click(); d.querySelector('[data-tab="assets"]').click(); ok("assets tab: 3 class panels", d.querySelectorAll("#assetCols .pan").length === 3); ok("assets: sparkline drawn", !!d.querySelector("#assetCols .spark svg path")); ok("assets: gold shows BUY + chart verdict", [...d.querySelectorAll("#assetCols .vchip")].some((c) => /Chart: confirm/.test(c.textContent)) && !!d.querySelector("#assetCols .chip.buy")); ok("assets: unavailable feed labelled", /no live data/.test(d.getElementById("assetCols").textContent));
  d.querySelector('[data-tab="ipos"]').click(); ok("ipos: 2 cards", d.querySelectorAll("#ipoList .card").length === 2); ok("ipos: favourable chip", !!d.querySelector("#ipoList .v-confirm")); ok("ipos: insufficient-data warning, no scores", /Not enough fundamentals/.test(d.getElementById("ipoList").textContent) && d.querySelectorAll("#ipoList .sbar").length === 2); ok("ipos: GMP labelled unofficial", /unofficial, not scored/.test(d.getElementById("ipoList").textContent)); ok("ipos: factors explain scores", !!d.querySelector("#ipoList details li"));
  d.querySelector('[data-tab="track"]').click(); await wait(200);
  ok("track: headline with n, hit rate and 95% range", /Of 196 settled calls on 130 separate days, 58%/.test(d.getElementById("trackBody").textContent) && /95% range/.test(d.getElementById("trackBody").textContent));
  ok("track: explains why the range is wide (same-day calls move together)", /same day move together/.test(d.getElementById("trackBody").textContent) && /about as much evidence as 85 independent/.test(d.getElementById("trackBody").textContent) && /a simple formula would show/.test(d.getElementById("trackBody").textContent));
  ok("track: thin buckets say how many separate days are needed instead of a rate", /22 calls, 15 days \(need 30 over 20\)/.test(d.getElementById("trackBody").textContent));
  ok("track: benchmark context shown", /index itself rose in 54%/.test(d.getElementById("trackBody").textContent));
  ok("track: strength table has 6 populated buckets, small ones dimmed", d.querySelectorAll("#trackBody table")[0].querySelectorAll("tbody tr").length === 6 && d.querySelectorAll("#trackBody table")[0].querySelectorAll("tr.dim").length === 2);
  ok("track: chart A/B and filter A/B tables", d.querySelectorAll("#trackBody table").length === 4 && /CONTRADICT/.test(d.getElementById("trackBody").textContent));
  ok("track: predictive verdict + honest note", /Within the same day/.test(d.getElementById("trackBody").textContent) && /excl\w+ costs/.test(d.getElementById("trackBody").textContent));
  ok("theme health: table with demoted theme and plain-language rule", d.querySelectorAll("#trackBody table").length === 4 && /Demoted/.test(d.getElementById("trackBody").textContent) && /upper 99% bound/.test(d.getElementById("trackBody").textContent));
  ok("theme health: unproven theme is NOT demoted, young theme dimmed", (() => { const rows = [...d.querySelectorAll("#trackBody table")[3].querySelectorAll("tbody tr")]; const r = (n) => rows.find((x) => x.textContent.includes(n)); return /unproven/.test(r("AI boom").textContent) && !/Demoted/.test(r("AI boom").textContent) && r("Gold bid").classList.contains("dim"); })());
  ok("theme health: audit log shows the reason", /clearly not beating the benchmark/.test(d.getElementById("trackBody").textContent) && /unproven → poor/.test(d.getElementById("trackBody").textContent));
  d.querySelector('#hz [data-h="20"]').click(); await wait(200); ok("track: horizon switch re-renders", /over 20 trading days/.test(d.getElementById("trackBody").textContent));
  ok("universe fallback warning on main screen", !d.getElementById("uniWarn").hidden && /113 stocks/.test(d.getElementById("uniWarn").textContent));
  d.querySelector('[data-tab="discovery"]').click(); await wait(150);
  ok("discovery: 2 picks and 1 watchlist card", d.querySelectorAll("#discPicks .card").length === 2 && d.querySelectorAll("#discWatch .card").length === 1);
  ok("discovery: quiet stock explained with numbers and no-headline flag", /returns on equity \(31%\)/.test(d.getElementById("discPicks").textContent) && /no headlines in the current news window/.test(d.getElementById("discPicks").textContent));
  ok("discovery: five factor bars per card", d.querySelectorAll("#discPicks .card")[0].querySelectorAll(".fbars .sbar").length === 5);
  ok("discovery: price-only name is watchlist only and flagged", /price-only: fundamentals not available/.test(d.getElementById("discWatch").textContent) && !!d.querySelector("#discWatch .chip.watch"));
  ok("discovery: extended pick says wait with zone", /wait for a pullback \(zone 1110-1180\)/.test(d.getElementById("discPicks").textContent));
  ok("discovery: honest track line and not-a-prediction note", /not enough history yet \(12 of 30/.test(d.getElementById("discNote").textContent) && /not a prediction/.test(d.getElementById("discNotes").textContent));
  ok("discovery: warns that fundamentals are partial/fallback list", /built-in 113-name fallback/.test(d.getElementById("discWarn").textContent));
  d.querySelector('[data-tab="market"]').click(); ok("market tab: 3 panels", d.querySelectorAll("#mktCols .pan").length === 3); ok("market tab: source note", /NSE: 497 quotes/.test(d.getElementById("mktNote").textContent)); d.querySelector('#mktCols .q[data-t="ONGC"]').click(); ok("market row opens drawer for board stock", d.getElementById("drawer").hidden === false); d.getElementById("dClose").click();
  d.querySelector('[data-tab="signals"]').click(); ok("chart-confirmed filter", d.querySelectorAll("#list .row").length === 1);
  dom.window.close();
  // 2) live mode with malicious payload (XSS)
  const evil = { count: 1, updated_at: new Date().toISOString(), board: [{ ticker: "<img src=x onerror=window.__pwn=1>", action: "BUY", net: 3, conviction: 4, why: "<script>window.__pwn=1</script>", headlines: ["<b>x</b>"], sources: [] }],
    news: [{ id: "e", source: "<i>s</i>", title: "<img src=x onerror=window.__pwn=1>", url: "javascript:window.__pwn=1", meaning: "<svg onload=window.__pwn=1>", direct_buy: [{ ticker: "<u>T</u>" }] }], coverage: [] };
  dom = await JSDOM.fromURL("http://127.0.0.1:8765/index.html", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse: (w) => { pre(w); w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(evil) }); w.EventSource = function () { setTimeout(() => this.onopen && this.onopen(), 0); }; } });
  await wait(600); d = dom.window.document;
  d.querySelector('[data-tab="tape"]').click(); await wait(100);
  ok("XSS: no injected elements", !d.querySelector("#list img, #list script, #feed img, #feed svg, #feed b, #feed i"));
  ok("XSS: no script executed", dom.window.__pwn === undefined);
  ok("XSS: javascript: URL neutralised", d.querySelector("#feed h3 a").getAttribute("href") === "#");
  ok("live status shows connected", d.getElementById("live").classList.contains("on"));
  dom.window.close();
  console.log(results.join("\n")); console.log(errors.length ? "RUNTIME ERRORS:\n" + errors.join("\n") : "no runtime errors");
  process.exit(results.some((r) => r.startsWith("FAIL")) || errors.length ? 1 : 0);
})();
