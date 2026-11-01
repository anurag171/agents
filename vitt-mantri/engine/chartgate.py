"""Wire the chart-confirmation engine (technicals.py) into live data: board rows and asset rows."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .bars import fetch_bars, yahoo_symbol
from .technicals import analog_stats, analyze, apply_gate, chart_payload, confirm


def evaluate(bars, action, require=False):
    """-> (verdict dict, summary dict for the UI, new_action, reason, conviction_multiplier)."""
    d = 1 if action == "BUY" else -1
    tech = analyze(bars, action)
    analog = analog_stats(bars, d) if tech.get("available") else None
    v = confirm(tech, analog)
    new, reason, mult = apply_gate(action, v, require_confirmation=require)
    s = {"verdict": v["verdict"], "score": v["score"], "reasons": v["reasons"], "as_of": tech.get("as_of"),
         **{k: tech.get(k) for k in ("trend", "rsi", "atr", "support", "resistance", "entry_zone", "stop", "target", "rr")},
         "analog": {k: analog.get(k) for k in ("n", "hit_rate", "median_ret")} if analog and analog.get("n") else None}
    return v, s, new, reason, mult


def enrich_board(board, fetch=fetch_bars, max_rows=15, require=False):
    rows = sorted((r for r in board if r["action"] in ("BUY", "SELL")), key=lambda r: -float(r.get("conviction") or 0))[:max_rows]
    syms = {r["ticker"]: yahoo_symbol(r["ticker"], (r.get("market") or {}).get("mkt") or ("US" if r.get("regions") == ["US"] else "IN")) for r in rows}
    with ThreadPoolExecutor(max_workers=6) as pool:
        data = dict(zip(syms, pool.map(lambda s: fetch(s), syms.values())))
    for r in rows:
        d = data.get(r["ticker"])
        if not d or len(d["bars"]) < 60:
            r["chart"] = {"verdict": "UNAVAILABLE", "reasons": ["no price history available"]}
            if require:
                r["news_action"], r["action"] = r["action"], "WATCH"; r["why"] = f"{r.get('why') or ''} [no chart data to confirm]".strip()
            continue
        _, summary, new, reason, mult = evaluate(d["bars"], r["action"], require)
        r["chart"] = summary
        if new != r["action"]:
            r["news_action"], r["action"] = r["action"], new
            r["why"] = f"{r.get('why') or ''} [{reason}]".strip()
        r["conviction"] = max(1, round(float(r.get("conviction") or 1) * mult))
    return board


def chart_for(ticker, action, mkt="IN", fetch=fetch_bars):
    d = fetch(yahoo_symbol(ticker, mkt))
    if not d:
        return None
    return chart_payload(d["bars"], analyze(d["bars"], action))
