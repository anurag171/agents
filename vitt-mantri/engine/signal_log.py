"""Log every call, settle it against what the stock did afterwards, and turn the % into an observed hit rate.

WHAT IS LOGGED  one row when the NEWS-implied direction for a ticker changes to BUY/SELL (a new call or a flip), not every
  90 s cycle (that would count one call hundreds of times). Calls the strength gate / chart / priced-in check downgraded are
  logged too ("shadow calls": news_action BUY, final_action WATCH) - otherwise we could never tell whether those filters help.
HOW IT IS SETTLED  entry = the price shown when the call was emitted; exit = the CLOSE h trading days after the emission
  date (h = 1, 5, 20). The emission-day close is never used as an exit, and a move before the call can't count (no look-ahead).
  "Right" = moved in the called direction. Also stored: excess over a benchmark (Nifty 50 / S&P 500) to separate skill from
  a rising market. Costs, slippage and taxes are NOT included.
HOW IT IS REPORTED  per strength bucket: n, hit rate with a Wilson 95% interval, average return/excess. Below MIN_N calls we say
  "not enough history" instead of printing a percentage. A permutation-tested rank correlation says whether strength predicts
  outcomes at all, and filters are compared (A/B) on the same shadow data. Rows are versioned (scoring_version) so a changed
  formula never mixes with old calls."""
from __future__ import annotations

import json
import math
import random
import statistics
from datetime import date, datetime, timedelta, timezone

from .bars import fetch_bars, yahoo_symbol
from .scoring import SCORING_VERSION
from .store import Store

HORIZONS, HEADLINE_H = (1, 5, 20), 5
MIN_N, MIN_REENTRY_DAYS, SPLIT_GUARD = 30, 5, 0.30
EDGES = [(0, "0-39%"), (40, "40-49%"), (50, "50-59%"), (60, "60-69%"), (70, "70-79%"), (80, "80-89%"), (90, "90-100%")]
BENCH = {"IN": "^NSEI", "US": "^GSPC"}


def bucket(p) -> str:
    return [lab for lo, lab in EDGES if p >= lo][-1]


def _now():
    return datetime.now(timezone.utc)


def call_direction(r: dict) -> int:
    a = r.get("news_action") or r.get("action")
    return 1 if a == "BUY" else -1 if a == "SELL" else 0


def _gate_reason(r):
    if r.get("gated_by_strength"): return "strength"
    if r.get("priced_in"): return "priced_in"
    v = (r.get("chart") or {}).get("verdict")
    return f"chart:{v.lower()}" if r.get("action") == "WATCH" and v in ("WAIT", "CONTRADICT") else None


# ---------------------------------------------------------------- logging
def record(store: Store, board: list, assets: list, now: datetime | None = None, engine_version: str = "dev") -> int:
    now, new = now or _now(), 0
    with store.conn() as c:
        for kind, rows in (("stock", board), ("asset", assets)):
            for r in rows:
                ticker = r.get("ticker") if kind == "stock" else r.get("key")
                if not ticker:
                    continue
                d = call_direction(r)
                st = c.execute("SELECT direction FROM signal_state WHERE kind=? AND ticker=?", (kind, ticker)).fetchone()
                if d == 0:                                                   # call ended / never existed
                    if st and st["direction"] != 0:
                        c.execute("UPDATE signal_state SET direction=0, since=? WHERE kind=? AND ticker=?", (now.isoformat(), kind, ticker))
                    continue
                if st and st["direction"] == d:                              # same call still standing: do not re-log
                    continue
                c.execute("INSERT INTO signal_state(kind,ticker,direction,since) VALUES(?,?,?,?) ON CONFLICT(kind,ticker) DO UPDATE SET direction=excluded.direction, since=excluded.since",
                          (kind, ticker, d, now.isoformat()))
                last = c.execute("SELECT MAX(emitted_at) m FROM signals_log WHERE kind=? AND ticker=? AND direction=?", (kind, ticker, d)).fetchone()["m"]
                if last and (now - datetime.fromisoformat(last)).days < MIN_REENTRY_DAYS:
                    continue                                                 # flicker: BUY->WATCH->BUY within days is one call
                mk, ch = r.get("market") or {}, r.get("chart") or {}
                mkt = mk.get("mkt") or ("US" if r.get("regions") == ["US"] else "IN") if kind == "stock" else None
                yahoo = yahoo_symbol(ticker, mkt) if kind == "stock" else r.get("yahoo")
                price = mk.get("price") or ch.get("close") or r.get("price")
                ev = {"headlines": (r.get("headlines") or [])[:3], "sources": (r.get("sources") or [])[:6], "evidence": [e.get("theme") for e in r.get("evidence") or []][:4]}
                cur = c.execute("""INSERT OR IGNORE INTO signals_log(emitted_at,emitted_date,kind,ticker,yahoo,mkt,direction,news_action,final_action,gate_reason,score_pct,strength,
                    coverage_pct,limited_data,net,chart_verdict,chart_score,market_change_pct,priced_in,best_grade,n_events,n_sources,price_at_signal,breakdown_json,evidence_json,
                    scoring_version,engine_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (now.isoformat(), now.date().isoformat(), kind, ticker, yahoo, mkt, d, r.get("news_action") or r["action"], r["action"], _gate_reason(r),
                                    r.get("score_pct"), r.get("strength"), r.get("data_coverage_pct"), int(bool(r.get("limited_data"))), r.get("net") if kind == "stock" else r.get("news_score"),
                                    ch.get("verdict"), ch.get("score"), mk.get("change_pct"), int(bool(r.get("priced_in"))), r.get("best_grade"), r.get("events"), len(r.get("sources") or []),
                                    price, json.dumps(r.get("signal_breakdown") or []), json.dumps(ev), r.get("scoring_version") or SCORING_VERSION, engine_version))
                new += cur.rowcount
    return new


# ---------------------------------------------------------------- settling
def _first_after(bars, d0: date) -> int:
    for i, b in enumerate(bars):
        if b.d > d0:
            return i
    return len(bars)


def compute_outcome(bars, d0: date, direction: int, entry_logged, h: int, bench_bars=None):
    """None until h trading days after d0 exist. Entry = logged price (guarded against splits), exit = close of the h-th trading day AFTER d0."""
    i0 = _first_after(bars, d0); j = i0 + h - 1
    if i0 == 0 or j >= len(bars):
        return None
    prev = bars[i0 - 1].c
    if entry_logged and abs(entry_logged / prev - 1) <= SPLIT_GUARD:
        entry, src = float(entry_logged), "logged"
    else:
        entry, src = prev, "bars_close"
    ret = (bars[j].c / entry - 1) * 100
    out = {"entry_price": entry, "entry_source": src, "exit_date": bars[j].d.isoformat(), "exit_price": bars[j].c, "ret_pct": ret, "signed_ret_pct": ret * direction,
           "bench_ret_pct": None, "signed_excess_pct": None}
    if bench_bars:
        b0 = _first_after(bench_bars, d0); bj = b0 + h - 1
        if b0 > 0 and bj < len(bench_bars):
            br = (bench_bars[bj].c / bench_bars[b0 - 1].c - 1) * 100
            out.update(bench_ret_pct=br, signed_excess_pct=(ret - br) * direction)
    return out


def evaluate_outcomes(store: Store, fetch=fetch_bars, today: date | None = None, max_symbols: int = 40) -> dict:
    today = today or _now().date()
    with store.conn() as c:
        todo = c.execute(f"""SELECT s.id, s.yahoo, s.mkt, s.kind, s.direction, s.emitted_date, s.price_at_signal, h.h AS horizon FROM signals_log s
                 JOIN (SELECT 1 h UNION SELECT 5 UNION SELECT 20) h LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon=h.h
                 WHERE o.signal_id IS NULL AND s.yahoo IS NOT NULL ORDER BY s.emitted_date""").fetchall()
    due = [t for t in todo if (today - date.fromisoformat(t["emitted_date"])).days >= t["horizon"]]
    by_sym = {}
    for t in due:
        by_sym.setdefault(t["yahoo"], []).append(t)
    bars_cache, settled = {}, 0
    def bars(sym):
        if sym not in bars_cache:
            d = fetch(sym, rng="2y"); bars_cache[sym] = d["bars"] if d else None
        return bars_cache[sym]
    for sym in list(by_sym)[:max_symbols]:
        b = bars(sym)
        if not b:
            continue
        for t in by_sym[sym]:
            bench = bars(BENCH[t["mkt"]]) if t["kind"] == "stock" and t["mkt"] in BENCH else None
            o = compute_outcome(b, date.fromisoformat(t["emitted_date"]), t["direction"], t["price_at_signal"], t["horizon"], bench)
            if not o:
                continue
            with store.conn() as c:
                c.execute("INSERT OR IGNORE INTO signal_outcomes(signal_id,horizon,entry_price,entry_source,exit_date,exit_price,ret_pct,signed_ret_pct,bench_ret_pct,signed_excess_pct,settled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (t["id"], t["horizon"], o["entry_price"], o["entry_source"], o["exit_date"], o["exit_price"], o["ret_pct"], o["signed_ret_pct"], o["bench_ret_pct"], o["signed_excess_pct"], _now().isoformat()))
            settled += 1
    return {"due": len(due), "settled": settled, "symbols": len(by_sym)}


# ---------------------------------------------------------------- statistics
def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return [0.0, 0.0]
    p, d = k / n, 1 + z * z / n
    c, m = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - m) / d, 4), round((c + m) / d, 4)]


def stats(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "enough": False}
    sr = [r["signed_ret_pct"] for r in rows]; hits = sum(1 for x in sr if x > 0)
    ex = [r["signed_excess_pct"] for r in rows if r["signed_excess_pct"] is not None]
    out = {"n": n, "enough": n >= MIN_N, "hit_rate": round(hits / n, 4), "hit_ci": wilson(hits, n), "mean_ret": round(statistics.fmean(sr), 3), "median_ret": round(statistics.median(sr), 3)}
    if ex:
        out.update(n_excess=len(ex), excess_hit_rate=round(sum(1 for x in ex if x > 0) / len(ex), 4), mean_excess=round(statistics.fmean(ex), 3))
    return out


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    rx, ry = _ranks(x), _ranks(y); mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry)); den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def predictive_test(rows: list, shuffles: int = 2000, seed: int = 7) -> dict:
    pts = [(r["score_pct"], r["signed_ret_pct"]) for r in rows if r["score_pct"] is not None]
    n = len(pts)
    if n < 50:
        return {"n": n, "verdict": f"Too early to judge whether strength predicts outcomes (n={n}, need 50)."}
    x, y = [p[0] for p in pts], [p[1] for p in pts]; ic = spearman(x, y); rnd, ge = random.Random(seed), 0
    for _ in range(shuffles):
        yy = y[:]; rnd.shuffle(yy); ge += spearman(x, yy) >= ic
    p = (ge + 1) / (shuffles + 1)
    good = ic > 0 and p < 0.05
    return {"n": n, "ic": round(ic, 3), "p_value": round(p, 4), "predictive": good,
            "verdict": (f"Higher strength has gone with better outcomes so far (rank correlation {ic:+.2f}, p={p:.3f}, n={n})." if good else
                        f"Strength has NOT been shown to predict outcomes (rank correlation {ic:+.2f}, p={p:.2f}, n={n}).")}


def performance(store: Store, horizon: int = HEADLINE_H, scoring_version: str = SCORING_VERSION, kind: str | None = None) -> dict:
    q = """SELECT s.*, o.signed_ret_pct, o.signed_excess_pct, o.bench_ret_pct FROM signals_log s JOIN signal_outcomes o ON o.signal_id=s.id
           WHERE o.horizon=? AND s.scoring_version=?""" + (" AND s.kind=?" if kind else "")
    with store.conn() as c:
        rows = [dict(r) for r in c.execute(q, (horizon, scoring_version) + ((kind,) if kind else ())).fetchall()]
        total = c.execute("SELECT COUNT(*) n FROM signals_log WHERE scoring_version=?", (scoring_version,)).fetchone()["n"]
    group = lambda key: {k: stats([r for r in rows if key(r) == k]) for k in sorted({key(r) for r in rows if key(r) is not None})}
    bench_up = [r["bench_ret_pct"] > 0 for r in rows if r["bench_ret_pct"] is not None]
    return {"horizon": horizon, "scoring_version": scoring_version, "min_n": MIN_N, "logged": total, "settled": len(rows), "pending": total - len(rows),
            "overall": stats(rows), "benchmark_up_rate": round(sum(bench_up) / len(bench_up), 4) if bench_up else None,
            "by_bucket": {lab: stats([r for r in rows if r["score_pct"] is not None and bucket(r["score_pct"]) == lab]) for _, lab in EDGES},
            "by_chart": group(lambda r: r["chart_verdict"] or "NO CHART"),
            "by_shown": {"shown as BUY/SELL": stats([r for r in rows if r["final_action"] in ("BUY", "SELL")]), "held back to WATCH": stats([r for r in rows if r["final_action"] == "WATCH"])},
            "by_direction": group(lambda r: "BUY" if r["direction"] > 0 else "SELL"), "by_gate_reason": group(lambda r: r["gate_reason"]),
            "predictive": predictive_test(rows),
            "notes": ["'Right' = price moved in the called direction over the horizon; returns exclude costs, slippage and taxes.",
                      "Calls are logged when the direction changes, not every cycle; each call is counted once.",
                      "Small samples and one market regime can mislead; intervals are 95% Wilson."]}


def history_sentence(b: dict, horizon: int):
    if not b or not b.get("n"):
        return None
    if not b["enough"]:
        return f"Not enough history yet ({b['n']} of {MIN_N} settled calls at this strength)."
    lo, hi = b["hit_ci"]
    return (f"Calls at this strength went the called way {b['hit_rate']:.0%} of the time over {horizon} trading days "
            f"(n={b['n']}, 95% range {lo:.0%}-{hi:.0%}); average {b['mean_ret']:+.1f}%.")


def attach_history(rows: list, perf: dict, id_key: str = "score_pct") -> list:
    for r in rows:
        p = r.get(id_key)
        if p is None:
            continue
        b = perf["by_bucket"].get(bucket(p), {})
        r["history"] = {"horizon": perf["horizon"], "bucket": bucket(p), "n": b.get("n", 0), "enough": bool(b.get("enough")), "needed": MIN_N,
                        "hit_rate": b.get("hit_rate"), "hit_ci": b.get("hit_ci"), "mean_ret": b.get("mean_ret"), "text": history_sentence(b, perf["horizon"]) or f"No settled calls yet at this strength (need {MIN_N})."}
    return rows


def export_csv(store: Store, limit: int = 5000) -> str:
    import csv, io
    with store.conn() as c:
        rows = c.execute("""SELECT s.id,s.emitted_at,s.kind,s.ticker,s.direction,s.news_action,s.final_action,s.gate_reason,s.score_pct,s.strength,s.coverage_pct,s.chart_verdict,s.market_change_pct,
            s.price_at_signal,s.scoring_version,o.horizon,o.exit_date,o.ret_pct,o.signed_ret_pct,o.bench_ret_pct,o.signed_excess_pct FROM signals_log s LEFT JOIN signal_outcomes o ON o.signal_id=s.id
            ORDER BY s.id DESC LIMIT ?""", (limit,)).fetchall()
    buf = io.StringIO(); w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys()); w.writerows([tuple(r) for r in rows])
    return buf.getvalue()
