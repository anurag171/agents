// static/chart.js - dependency-free SVG mini-chart for /api/chart/<ticker>?action=BUY|SELL
// Draws close, SMA20/50, entry-zone band, support/resistance, stop, target. All numbers are coerced
// with Number() and no server string is interpolated into markup (XSS-safe).
(function (root) {
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : null);

  function path(vals, x, y) {
    let d = "", pen = false;
    vals.forEach((v, i) => {
      const n = num(v);
      if (n === null) { pen = false; return; }
      d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(n).toFixed(1);
      pen = true;
    });
    return d;
  }

  function renderChart(el, p, w = 340, h = 160) {
    const close = (p.close || []).map(num);
    if (close.length < 2) { el.textContent = "No chart data"; return; }
    const lv = p.levels || {}, zone = p.entry_zone;
    const extra = [lv.support, lv.resistance, lv.stop, lv.target, zone && zone[0], zone && zone[1]].map(num).filter((v) => v !== null);
    const all = close.concat(extra).filter((v) => v !== null);
    const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.06 || 1;
    const x = (i) => 6 + (i / (close.length - 1)) * (w - 12);
    const y = (v) => h - 6 - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * (h - 12);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Price chart with entry zone and levels"); svg.style.width = "100%";
    const add = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); svg.appendChild(e); return e; };
    if (zone && num(zone[0]) !== null && num(zone[1]) !== null) {
      const y1 = y(num(zone[1])), y0 = y(num(zone[0]));
      add("rect", { x: 6, y: Math.min(y1, y0), width: w - 12, height: Math.abs(y0 - y1), fill: "currentColor", opacity: 0.12 });
    }
    [["support", "#2a9d5c"], ["resistance", "#d0453a"], ["stop", "#d0453a"], ["target", "#2a9d5c"]].forEach(([k, c]) => {
      const v = num(lv[k]); if (v === null) return;
      add("line", { x1: 6, x2: w - 6, y1: y(v), y2: y(v), stroke: c, "stroke-width": 1, "stroke-dasharray": k === "stop" || k === "target" ? "2 3" : "5 3", opacity: 0.8 });
    });
    add("path", { d: path(p.sma50 || [], x, y), fill: "none", stroke: "#8a8fa0", "stroke-width": 1 });
    add("path", { d: path(p.sma20 || [], x, y), fill: "none", stroke: "#d4a017", "stroke-width": 1 });
    add("path", { d: path(close, x, y), fill: "none", stroke: "currentColor", "stroke-width": 1.6 });
    el.replaceChildren(svg);
  }
  root.renderChart = renderChart;
})(typeof window !== "undefined" ? window : globalThis);
