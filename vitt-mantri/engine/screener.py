"""News-independent screen: find stocks with strong fundamentals AND price strength, whether or not any headline mentions them.

Why these factors: profitability/quality, growth, reasonable valuation, 6-12 month relative-strength momentum and lower risk are the
characteristics with the longest published track record of explaining stock returns. They work over MONTHS, not days, are noisy, and
go through bad stretches; a screen finds CANDIDATES, it does not predict. Its own picks are logged (kind='screen') and settled at
20/60 trading days so the app measures whether they work instead of assuming it.

Method: each metric is ranked as a percentile within its sector peer group (>= SECTOR_MIN names, else the whole screened universe), so a bank
isn't compared with a software company. Factor = mean of its available metric percentiles. Composite uses the SAME shrink-toward-50-by-coverage
formula as the news score (scoring.pct_from_components), so unavailable data lowers the score instead of being guessed.
Hard filters: liquidity (average traded value), history length, positive earnings, price above its 200-day average for a PICK.
A symbol with no fundamentals can only reach the WATCHLIST (price-only), never be a PICK."""
from __future__ import annotations

import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .bars import fetch_bars, yahoo_symbol
from .chartgate import evaluate
from .scoring import label, pct_from_components

SCREEN_VERSION = "q1"
BARS_TTL = 6 * 3600                          # daily bars barely change intraday: reuse them across the progressive fundamentals fill
W = {"quality": 0.25, "growth": 0.20, "value": 0.15, "momentum": 0.30, "risk": 0.10}
LABEL = {"quality": "Quality", "growth": "Growth", "value": "Value", "momentum": "Momentum vs Nifty", "risk": "Stability"}
SECTOR_MIN, MIN_BARS = 8, 126
MIN_TRADED_VALUE = 5e7                      # INR per day (about 5 crore): below this a stock is too thin to act on
PICK_MIN, WATCH_MIN, PICK_COVERAGE, MAX_ROWS, CHART_TOP = 70, 60, 0.60, 25, 40


def price_metrics(bars, bench_bars=None):
    c = [b.c for b in bars]
    n = len(c)
    if n < MIN_BARS:
        return None
    ret6 = c[-1] / c[-126] - 1
    b6 = (bench_bars[-1].c / bench_bars[-126].c - 1) if bench_bars and len(bench_bars) >= 126 else None
    sma200 = statistics.fmean(c[-200:]) if n >= 200 else None
    sma50 = statistics.fmean(c[-50:]) if n >= 50 else None
    rets = [c[i] / c[i - 1] - 1 for i in range(n - 125, n)]
    peak, mdd = c[-252], 0.0
    for x in c[-252:]:
        peak = max(peak, x); mdd = max(mdd, 1 - x / peak)
    win = bars[-252:]
    hi, lo = max(b.h for b in win), min(b.l for b in win)
    tv = statistics.fmean(b.c * b.v for b in bars[-20:])
    return {"price": c[-1], "ret6": ret6, "rel6": None if b6 is None else ret6 - b6, "ret12_1": (c[-22] / c[-253] - 1) if n >= 253 else None,
            "trend_ok": bool(sma200 and sma50 and c[-1] > sma200 and sma50 > sma200), "above200": bool(sma200 and c[-1] > sma200),
            "vol": statistics.pstdev(rets) * math.sqrt(252), "maxdd": mdd, "tv": tv, "pos52": (c[-1] - lo) / (hi - lo) if hi > lo else 0.5}


def _ranks(vals: dict, higher: bool = True) -> dict:
    items = sorted(vals.items(), key=lambda kv: kv[1] if higher else -kv[1])
    out, n, i = {}, len(items), 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        for k in range(i, j + 1):
            out[items[k][0]] = ((i + j) / 2) / (n - 1) if n > 1 else 0.5
        i = j + 1
    return out


def peer_ranks(vals: dict, groups: dict, higher: bool = True) -> dict:
    """Percentile within the sector group when it has >= SECTOR_MIN scored names, otherwise against the whole screened universe."""
    ranks = _ranks(vals, higher)
    by_group = {}
    for s in vals:
        by_group.setdefault(groups.get(s) or "ALL", []).append(s)
    for g, members in by_group.items():
        if g != "ALL" and len(members) >= SECTOR_MIN:
            ranks.update(_ranks({s: vals[s] for s in members}, higher))
    return ranks


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def screen(items: list, pm: dict, funds: dict, news_symbols=(), weights=None, min_tv: float = MIN_TRADED_VALUE) -> dict:
    """items: [{ticker,name,sector}]; pm: price metrics by ticker; funds: fundamentals by ticker. Pure function (no network)."""
    weights = weights or W
    elig = [i for i in items if i["ticker"] in pm and pm[i["ticker"]]["tv"] >= min_tv]
    groups = {i["ticker"]: (funds.get(i["ticker"], {}).get("sector") or i.get("sector") or "").lower() for i in elig}
    fin = {t for t, g in groups.items() if "financ" in g or "bank" in g}
    col = lambda f: {i["ticker"]: f(i["ticker"]) for i in elig if f(i["ticker"]) is not None}
    F = lambda t, k: funds.get(t, {}).get(k)
    R = {
        "roe": peer_ranks(col(lambda t: F(t, "roe")), groups), "opm": peer_ranks(col(lambda t: F(t, "op_margin")), groups),
        "de": peer_ranks(col(lambda t: F(t, "debt_equity") if t not in fin else None), groups, higher=False),
        "rev": peer_ranks(col(lambda t: F(t, "rev_growth")), groups), "earn": peer_ranks(col(lambda t: F(t, "earn_growth")), groups),
        "pe": peer_ranks(col(lambda t: F(t, "pe") if (F(t, "pe") or 0) > 0 else None), groups, higher=False),
        "pb": peer_ranks(col(lambda t: F(t, "pb") if (F(t, "pb") or 0) > 0 else None), groups, higher=False),
        "peg": peer_ranks(col(lambda t: F(t, "peg") if (F(t, "peg") or 0) > 0 else None), groups, higher=False),
        "rel6": peer_ranks(col(lambda t: pm[t]["rel6"]), groups), "r12": peer_ranks(col(lambda t: pm[t]["ret12_1"]), groups),
        "vol": peer_ranks(col(lambda t: pm[t]["vol"]), groups, higher=False), "mdd": peer_ranks(col(lambda t: pm[t]["maxdd"]), groups, higher=False)}
    rows = []
    for i in elig:
        t, p, f = i["ticker"], pm[i["ticker"]], funds.get(i["ticker"], {})
        loss = (f.get("profit_margin") is not None and f["profit_margin"] < 0) or (f.get("earn_growth") is None and f.get("pe") is None and f.get("roe") is not None and f["roe"] < 0)
        g = lambda k: R[k].get(t)
        mom = _mean([g("rel6"), g("r12")])
        comps = {"quality": _mean([g("roe"), g("opm"), g("de")]), "growth": _mean([g("rev"), g("earn")]),
                 "value": (0.0 if loss else _mean([g("pe"), g("pb"), g("peg")])), "momentum": None if mom is None else mom * (1.0 if p["trend_ok"] else 0.7),
                 "risk": _mean([g("vol"), g("mdd")])}
        raw, cov = pct_from_components(comps, weights)
        score, price_only = int(round(raw)), all(comps[k] is None for k in ("quality", "growth", "value"))
        flags = []
        if price_only: flags.append("price-only: fundamentals not available")
        if loss: flags.append("loss-making")
        if t not in news_symbols: flags.append("no headlines in the current news window")
        if p["pos52"] >= 0.95: flags.append("at 52-week high")
        if not p["above200"]: flags.append("below its 200-day average")
        tier = None
        if not loss and score >= PICK_MIN and cov >= PICK_COVERAGE and p["trend_ok"] and not price_only: tier = "PICK"
        elif not loss and score >= WATCH_MIN: tier = "WATCHLIST"
        if tier is None:
            continue
        facts = {"roe": f.get("roe"), "debt_equity": f.get("debt_equity"), "rev_growth": f.get("rev_growth"), "earn_growth": f.get("earn_growth"), "pe": f.get("pe"),
                 "ret6_pct": round(p["ret6"] * 100, 1), "rel6_pct": None if p["rel6"] is None else round(p["rel6"] * 100, 1), "vol_pct": round(p["vol"] * 100), "maxdd_pct": round(p["maxdd"] * 100)}
        rows.append({"ticker": t, "name": i.get("name") or t, "sector": groups[t] or "", "tier": tier, "score_pct": score, "strength": label(min(score, 79) if cov < 0.7 else score),
                     "coverage_pct": int(round(cov * 100)), "price_only": price_only, "price": round(p["price"], 2), "facts": facts, "flags": flags,
                     "factors": {k: (None if v is None else int(round(v * 100))) for k, v in comps.items()},
                     "breakdown": [{"key": k, "label": LABEL[k], "weight_pct": int(weights[k] * 100), "available": comps[k] is not None, "value_pct": None if comps[k] is None else int(round(comps[k] * 100)), "note": ""} for k in weights],
                     "no_news": t not in news_symbols, "why": _why(facts, comps, t not in news_symbols)})
    rows.sort(key=lambda r: (-r["score_pct"], r["ticker"]))
    return {"rows": rows, "screened": len(items), "eligible": len(elig), "with_fundamentals": sum(1 for i in elig if funds.get(i["ticker"])), "min_traded_value": min_tv}


def _why(f, comps, no_news):
    bits = []
    if (comps["quality"] or 0) >= 0.7:
        bits.append(f"strong returns on equity ({f['roe']:.0f}%)" if f.get("roe") is not None else "strong profitability")
        if f.get("debt_equity") is not None and f["debt_equity"] < 0.5: bits.append(f"low debt (D/E {f['debt_equity']:.1f})")
    if (comps["growth"] or 0) >= 0.7:
        g = [x for x in (f.get("rev_growth"), f.get("earn_growth")) if x is not None]
        bits.append(f"growing fast ({max(g):+.0f}%)" if g else "strong growth")
    if (comps["value"] or 0) >= 0.7 and f.get("pe"): bits.append(f"cheap against peers (P/E {f['pe']:.0f})")
    if (comps["momentum"] or 0) >= 0.7 and f.get("rel6_pct") is not None: bits.append(f"beating the Nifty by {f['rel6_pct']:+.0f}% over 6 months")
    if (comps["risk"] or 0) >= 0.7: bits.append("steadier than most")
    text = ", ".join(bits) if bits else "balanced profile across quality, growth, value and momentum"
    return text[0].upper() + text[1:] + ("; barely in the headlines." if no_news else ".")


def run_screen(store, universe, *, fetch=fetch_bars, load_fund=None, news_symbols=(), max_symbols: int = 600, now=None, workers: int = 8) -> dict:
    from .fundamentals import load_fundamentals
    load_fund = load_fund or load_fundamentals
    now = now or datetime.now(timezone.utc)
    inst = sorted((i for i in universe.by_symbol.values() if i.mkt == "IN" and i.asset_class == "equity" and i.tier <= 3), key=lambda i: (i.tier, i.symbol))[:max_symbols]
    bench = fetch("^NSEI", rng="2y", ttl=BARS_TTL); bench_bars = bench["bars"] if bench else None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        data = list(pool.map(lambda i: fetch(yahoo_symbol(i.symbol, "IN"), rng="2y", ttl=BARS_TTL), inst))
    pm = {}
    for i, d in zip(inst, data):
        m = price_metrics(d["bars"], bench_bars) if d and d.get("bars") else None
        if m:
            pm[i.symbol] = {**m, "_bars": d["bars"]}
    funds, finfo = load_fund(store, [i.symbol for i in inst])
    res = screen([{"ticker": i.symbol, "name": i.name, "sector": i.industry} for i in inst], pm, funds, set(news_symbols))
    rows = res["rows"]
    for r in rows[:CHART_TOP]:                                            # same chart discipline as the news signals
        _, summ, _, _, _ = evaluate(pm[r["ticker"]]["_bars"], "BUY"); r["chart"] = summ
        if summ["verdict"] == "CONTRADICT" and r["tier"] == "PICK":
            r["tier"] = "WATCHLIST"; r["flags"].append("chart contradicts: moved to watchlist")
        elif summ["verdict"] == "WAIT":
            z = summ.get("entry_zone"); r["flags"].append("extended: wait for a pullback" + (f" (zone {z[0]}-{z[1]})" if z else ""))
    picks, watch = [r for r in rows if r["tier"] == "PICK"][:MAX_ROWS], [r for r in rows if r["tier"] == "WATCHLIST"][:MAX_ROWS]
    notes = ["A screen finds candidates by quality, growth, value and momentum. These factors work over months, not days, and go through long bad stretches.",
             "It is not a prediction. Picks are logged and checked against what the stocks did next; see the track line above."]
    if res["with_fundamentals"] < 0.3 * max(1, res["eligible"]):
        notes.append(f"Fundamentals are available for only {res['with_fundamentals']} of {res['eligible']} screened stocks, so most results are price-only. Add a fundamentals file (VM_FUNDAMENTALS_DIR) or let the Yahoo fill continue.")
    return {"as_of": now.isoformat(), "version": SCREEN_VERSION, "picks": picks, "watchlist": watch, "notes": notes,
            "universe": {"requested": len(inst), "with_prices": len(pm), "eligible": res["eligible"], "with_fundamentals": res["with_fundamentals"], "min_traded_value": MIN_TRADED_VALUE, "fundamentals_info": finfo}}
