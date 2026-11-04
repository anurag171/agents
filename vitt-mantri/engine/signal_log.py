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

import copy
import json
import math
import os
import random
import statistics
from datetime import date, datetime, timedelta, timezone

from .bars import fetch_bars, yahoo_symbol
from .scoring import SCORING_VERSION
from .store import Store

HORIZONS, HEADLINE_H = (1, 5, 20, 60), 5
MIN_N, MIN_REENTRY_DAYS, SPLIT_GUARD = 30, 5, 0.30
MIN_DAYS = int(os.getenv("VM_MIN_DAYS", "20"))      # separate emission days needed before a % is shown: 30 calls on 3 days is 3 data points, not 30
BOOT_ITERS = 600
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
    if r.get("demoted_theme"): return "theme_demoted"
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
                    scoring_version,engine_version,primary_theme,themes_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (now.isoformat(), now.date().isoformat(), kind, ticker, yahoo, mkt, d, r.get("news_action") or r["action"], r["action"], _gate_reason(r),
                                    r.get("score_pct"), r.get("strength"), r.get("data_coverage_pct"), int(bool(r.get("limited_data"))), r.get("net") if kind == "stock" else r.get("news_score"),
                                    ch.get("verdict"), ch.get("score"), mk.get("change_pct"), int(bool(r.get("priced_in"))), r.get("best_grade"), r.get("events"), len(r.get("sources") or []),
                                    price, json.dumps(r.get("signal_breakdown") or []), json.dumps(ev), r.get("scoring_version") or SCORING_VERSION, engine_version, r.get("primary_theme"), json.dumps(r.get("themes") or r.get("evidence") and [{"id": e.get("theme_id"), "label": e.get("theme")} for e in r["evidence"]] or [])))
                new += cur.rowcount
    return new


SCREEN_REENTRY_DAYS = 30


def record_screen(store: Store, picks: list, version: str, now: datetime | None = None, engine_version: str = "dev") -> int:
    """Log a screen PICK once, when it first qualifies (kind='screen'); settled at 20/60 days like any other call."""
    now, new = now or _now(), 0
    current = {p["ticker"] for p in picks if p.get("tier") == "PICK"}
    with store.conn() as c:
        for r in c.execute("SELECT ticker FROM signal_state WHERE kind='screen' AND direction=1").fetchall():
            if r["ticker"] not in current:
                c.execute("UPDATE signal_state SET direction=0, since=? WHERE kind='screen' AND ticker=?", (now.isoformat(), r["ticker"]))
        for p in picks:
            if p.get("tier") != "PICK":
                continue
            st = c.execute("SELECT direction FROM signal_state WHERE kind='screen' AND ticker=?", (p["ticker"],)).fetchone()
            if st and st["direction"] == 1:
                continue
            c.execute("INSERT INTO signal_state(kind,ticker,direction,since) VALUES('screen',?,1,?) ON CONFLICT(kind,ticker) DO UPDATE SET direction=1, since=excluded.since", (p["ticker"], now.isoformat()))
            last = c.execute("SELECT MAX(emitted_at) m FROM signals_log WHERE kind='screen' AND ticker=?", (p["ticker"],)).fetchone()["m"]
            if last and (now - datetime.fromisoformat(last)).days < SCREEN_REENTRY_DAYS:
                continue
            ch = p.get("chart") or {}
            cur = c.execute("""INSERT OR IGNORE INTO signals_log(emitted_at,emitted_date,kind,ticker,yahoo,mkt,direction,news_action,final_action,score_pct,strength,coverage_pct,limited_data,
                chart_verdict,chart_score,best_grade,price_at_signal,breakdown_json,evidence_json,scoring_version,engine_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now.isoformat(), now.date().isoformat(), "screen", p["ticker"], yahoo_symbol(p["ticker"], "IN"), "IN", 1, "BUY", "BUY", p.get("score_pct"), p.get("strength"), p.get("coverage_pct"),
                 int(bool(p.get("price_only"))), ch.get("verdict"), ch.get("score"), None, p.get("price"), json.dumps(p.get("breakdown") or []),
                 json.dumps({"why": p.get("why"), "flags": p.get("flags")}), version, engine_version))
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
                 JOIN (SELECT 1 h UNION SELECT 5 UNION SELECT 20 UNION SELECT 60) h LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon=h.h
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
            bench = bars(BENCH[t["mkt"]]) if t["kind"] in ("stock", "screen") and t["mkt"] in BENCH else None
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


def _day_key(r, i):
    return r.get("emitted_date") or f"row{i}"


def cluster_bootstrap(rows: list, iters: int = BOOT_ITERS, seed: int = 11, conf: float = 0.95):
    """Resample whole DAYS with replacement. Calls made on the same day share the market's mood (one Brent headline -> six calls;
    a risk-off day sinks every BUY), so they are not independent: a simple formula sees 8 calls where there is closer to 1 data point.
    Returns None with fewer than 5 days (a bootstrap over so few clusters would itself be unreliable)."""
    by = {}
    for i, r in enumerate(rows):
        c = by.setdefault(_day_key(r, i), [0, 0, 0.0, 0, 0.0])
        y = r["signed_ret_pct"]; c[0] += 1; c[1] += y > 0; c[2] += y
        e = r.get("signed_excess_pct")
        if e is not None:
            c[3] += 1; c[4] += e
    cl = list(by.values()); k = len(cl)
    if k < 5:
        return None
    rnd, hr, mr, me = random.Random(seed), [], [], []
    for _ in range(iters):
        pick = rnd.choices(cl, k=k)
        n = sum(c[0] for c in pick); hr.append(sum(c[1] for c in pick) / n); mr.append(sum(c[2] for c in pick) / n)
        en = sum(c[3] for c in pick)
        if en:
            me.append(sum(c[4] for c in pick) / en)
    a = (1 - conf) / 2
    q = lambda xs, nd: [round(sorted(xs)[int(a * len(xs))], nd), round(sorted(xs)[min(len(xs) - 1, int((1 - a) * len(xs)))], nd)]
    return {"days": k, "hit": q(hr, 4), "hit_se": statistics.pstdev(hr), "ret": q(mr, 3), "excess": q(me, 3) if len(me) >= 0.9 * iters else None}


def stats(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "n_days": 0, "enough": False}
    sr = [r["signed_ret_pct"] for r in rows]; hits = sum(1 for x in sr if x > 0)
    ex = [r["signed_excess_pct"] for r in rows if r.get("signed_excess_pct") is not None]
    boot = cluster_bootstrap(rows)
    days = boot["days"] if boot else len({_day_key(r, i) for i, r in enumerate(rows)})
    naive = wilson(hits, n)
    out = {"n": n, "n_days": days, "enough": n >= MIN_N and days >= MIN_DAYS, "hit_rate": round(hits / n, 4),
           "hit_ci": boot["hit"] if boot else naive, "hit_ci_naive": naive, "ci_method": "bootstrap over days" if boot else "simple (too few days to cluster)",
           "mean_ret": round(statistics.fmean(sr), 3), "median_ret": round(statistics.median(sr), 3)}
    if boot:
        p = hits / n; se_srs = math.sqrt(p * (1 - p) / n) if 0 < p < 1 else 0.0
        deff = max(1.0, (boot["hit_se"] / se_srs) ** 2) if se_srs > 0 else 1.0
        out.update(design_effect=round(deff, 2), n_eff=round(n / deff, 1), ret_ci=boot["ret"])
    if ex:
        out.update(n_excess=len(ex), excess_hit_rate=round(sum(1 for x in ex if x > 0) / len(ex), 4), mean_excess=round(statistics.fmean(ex), 3))
        if boot and boot["excess"]:
            out["excess_ci"] = boot["excess"]
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
    """Does a higher strength % rank calls better THAN OTHER CALLS MADE THE SAME DAY? Ranks are taken within each day and outcomes are
    shuffled only within days. This removes market-wide effects (strong calls tend to cluster on bullish days, which would otherwise look
    like skill) and respects that same-day calls are not independent. Uses excess return over the benchmark where available."""
    by = {}
    for i, r in enumerate(rows):
        if r.get("score_pct") is None:
            continue
        y = r["signed_excess_pct"] if r.get("signed_excess_pct") is not None else r["signed_ret_pct"]
        by.setdefault(_day_key(r, i), []).append((r["score_pct"], y))
    days = []
    for pts in by.values():
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if len(pts) < 2 or len(set(xs)) < 2 or len(set(ys)) < 2:
            continue
        rx, ry = _ranks(xs), _ranks(ys); mx, my = statistics.fmean(rx), statistics.fmean(ry)
        days.append(([a - mx for a in rx], [b - my for b in ry]))
    n = sum(len(d[0]) for d in days)
    if len(days) < MIN_DAYS or n < 50:
        return {"n": n, "n_days": len(days), "verdict": f"Too early to judge whether higher strength ranks calls better (need 50 calls on at least {MIN_DAYS} days that each had several calls; have {n} calls on {len(days)} such days)."}
    den = math.sqrt(sum(a * a for cx, _ in days for a in cx) * sum(b * b for _, cy in days for b in cy))
    obs = sum(a * b for cx, cy in days for a, b in zip(cx, cy)) / den
    rnd, ge = random.Random(seed), 0
    for _ in range(shuffles):
        tot = 0.0
        for cx, cy in days:
            sh = cy[:]; rnd.shuffle(sh); tot += sum(a * b for a, b in zip(cx, sh))
        ge += tot / den >= obs
    p = (ge + 1) / (shuffles + 1)
    good = obs > 0 and p < 0.05
    return {"n": n, "n_days": len(days), "ic": round(obs, 3), "p_value": round(p, 4), "predictive": good,
            "verdict": (f"Within the same day, higher-strength calls have done better than lower-strength ones (rank correlation {obs:+.2f}, p={p:.3f}, {n} calls on {len(days)} days)." if good else
                        f"Strength has NOT been shown to rank calls better within a day (rank correlation {obs:+.2f}, p={p:.2f}, {n} calls on {len(days)} days).")}


_PERF_MEMO = {}


def performance(store: Store, horizon: int = HEADLINE_H, scoring_version: str = SCORING_VERSION, kind: str | None = None) -> dict:
    kf = " AND s.kind=?" if kind else ""
    args = (horizon, scoring_version) + ((kind,) if kind else ())
    with store.conn() as c:                                   # cheap fingerprint: recompute (bootstraps are not free) only when outcomes change
        sig = tuple(c.execute("SELECT COUNT(*) n, COALESCE(MAX(o.settled_at),'') m FROM signal_outcomes o JOIN signals_log s ON s.id=o.signal_id WHERE o.horizon=? AND s.scoring_version=?" + kf, args).fetchone()) + \
              (c.execute("SELECT COUNT(*) n FROM signals_log WHERE scoring_version=?", (scoring_version,)).fetchone()["n"],)
    memo = _PERF_MEMO.get((store.path, horizon, scoring_version, kind))
    if memo and memo[0] == sig:
        return copy.deepcopy(memo[1])
    q = """SELECT s.*, o.signed_ret_pct, o.signed_excess_pct, o.bench_ret_pct FROM signals_log s JOIN signal_outcomes o ON o.signal_id=s.id
           WHERE o.horizon=? AND s.scoring_version=?""" + kf
    with store.conn() as c:
        rows = [dict(r) for r in c.execute(q, (horizon, scoring_version) + ((kind,) if kind else ())).fetchall()]
        total = c.execute("SELECT COUNT(*) n FROM signals_log WHERE scoring_version=?", (scoring_version,)).fetchone()["n"]
    group = lambda key: {k: stats([r for r in rows if key(r) == k]) for k in sorted({key(r) for r in rows if key(r) is not None})}
    bench_up = [r["bench_ret_pct"] > 0 for r in rows if r["bench_ret_pct"] is not None]
    out = {"horizon": horizon, "scoring_version": scoring_version, "min_n": MIN_N, "min_days": MIN_DAYS, "logged": total, "settled": len(rows), "pending": total - len(rows),
            "overall": stats(rows), "benchmark_up_rate": round(sum(bench_up) / len(bench_up), 4) if bench_up else None,
            "by_bucket": {lab: stats([r for r in rows if r["score_pct"] is not None and bucket(r["score_pct"]) == lab]) for _, lab in EDGES},
            "by_chart": group(lambda r: r["chart_verdict"] or "NO CHART"),
            "by_shown": {"shown as BUY/SELL": stats([r for r in rows if r["final_action"] in ("BUY", "SELL")]), "held back to WATCH": stats([r for r in rows if r["final_action"] == "WATCH"])},
            "by_direction": group(lambda r: "BUY" if r["direction"] > 0 else "SELL"), "by_gate_reason": group(lambda r: r["gate_reason"]),
            "predictive": predictive_test(rows),
            "notes": ["'Right' = price moved in the called direction over the horizon; returns exclude costs, slippage and taxes.",
                      "Calls are logged when the direction changes, not every cycle; each call is counted once.",
                      f"Calls made on the same day move together, so ranges come from resampling whole days (a simple formula would be too narrow), and a percentage is shown only after {MIN_N} calls over at least {MIN_DAYS} separate days.",
                      "One market regime can still mislead: results from a single bullish or bearish stretch may not repeat."]}
    _PERF_MEMO[(store.path, horizon, scoring_version, kind)] = (sig, copy.deepcopy(out))
    return out


def history_sentence(b: dict, horizon: int):
    if not b or not b.get("n"):
        return None
    if not b["enough"]:
        return (f"Not enough history yet ({b['n']} of {MIN_N} settled calls at this strength)." if b["n"] < MIN_N else
                f"Not enough history yet ({b['n']} calls, but only on {b.get('n_days', 0)} separate days; need {MIN_N} calls over {MIN_DAYS} days).")
    lo, hi = b["hit_ci"]
    return (f"Calls at this strength went the called way {b['hit_rate']:.0%} of the time over {horizon} trading days "
            f"(n={b['n']} calls on {b['n_days']} days, 95% range {lo:.0%}-{hi:.0%}); average {b['mean_ret']:+.1f}%.")


def attach_history(rows: list, perf: dict, id_key: str = "score_pct") -> list:
    for r in rows:
        p = r.get(id_key)
        if p is None:
            continue
        b = perf["by_bucket"].get(bucket(p), {})
        r["history"] = {"horizon": perf["horizon"], "bucket": bucket(p), "n": b.get("n", 0), "n_days": b.get("n_days", 0), "enough": bool(b.get("enough")), "needed": MIN_N,
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


# ---------------------------------------------------------------- theme health & demotion
THEME_MIN_N = int(os.getenv("VM_THEME_MIN_N", "30"))          # INDEPENDENT observations (theme-days), not calls
THEME_WINDOW_DAYS, DEMOTE_CONF, REENABLE_MARGIN, MIN_DWELL_DAYS = 180, 0.99, 0.25, 14


def bootstrap_mean_ci(xs: list, conf: float = DEMOTE_CONF, iters: int = 2000, seed: int = 11):
    rnd, n = random.Random(seed), len(xs)
    means = sorted(statistics.fmean(rnd.choices(xs, k=n)) for _ in range(iters))
    a = (1 - conf) / 2
    return means[int(a * iters)], means[min(iters - 1, int((1 - a) * iters))]


def theme_observations(store: Store, horizon: int = HEADLINE_H, now: datetime | None = None) -> dict:
    """{(theme, kind): [one number per emission DATE]}. Calls made on the same day from the same theme (one Brent headline ->
    six tickers) move together, so they are averaged into ONE independent observation; counting them separately would
    overstate the evidence and demote/promote themes on noise. Metric: excess over the benchmark where available."""
    since = ((now or _now()) - timedelta(days=THEME_WINDOW_DAYS)).date().isoformat()
    with store.conn() as c:
        rows = c.execute("""SELECT s.primary_theme t, s.kind k, s.emitted_date d, COALESCE(o.signed_excess_pct, o.signed_ret_pct) y
                 FROM signals_log s JOIN signal_outcomes o ON o.signal_id=s.id
                 WHERE o.horizon=? AND s.primary_theme IS NOT NULL AND s.emitted_date>=?""", (horizon, since)).fetchall()
    cells = {}
    for r in rows:
        cells.setdefault((r["t"], r["k"], r["d"]), []).append(r["y"])
    out, ncalls = {}, {}
    for (t, k, d), ys in cells.items():
        out.setdefault((t, k), []).append(statistics.fmean(ys)); ncalls[(t, k)] = ncalls.get((t, k), 0) + len(ys)
    return {key: {"obs": v, "n_calls": ncalls[key]} for key, v in out.items()}


def classify(obs: list, prev_status: str | None, days_in_status: float, conf: float = DEMOTE_CONF):
    """building (<N independent obs) | proven (lower bound > 0) | unproven (interval straddles 0) | poor (upper bound <= 0).
    Only 'poor' demotes. A poor theme returns only after MIN_DWELL_DAYS and when its upper bound clears +REENABLE_MARGIN,
    so a theme hovering at the boundary cannot flap on and off."""
    n = len(obs)
    if n < THEME_MIN_N:
        return "building", None
    lo, hi = bootstrap_mean_ci(obs, conf)
    ci = (round(lo, 3), round(hi, 3))
    if prev_status == "poor":
        if days_in_status < MIN_DWELL_DAYS or hi <= REENABLE_MARGIN:
            return "poor", ci
    if hi <= 0:
        return "poor", ci
    return ("proven" if lo > 0 else "unproven"), ci


def update_theme_gates(store: Store, horizon: int = HEADLINE_H, now: datetime | None = None) -> list:
    now, changes = now or _now(), []
    obs = theme_observations(store, horizon, now)
    with store.conn() as c:
        prev = {(r["theme_id"], r["kind"]): r for r in c.execute("SELECT * FROM theme_gate").fetchall()}
        for (theme, kind), v in sorted(obs.items()):
            p = prev.get((theme, kind))
            days = (now - datetime.fromisoformat(p["since"])).total_seconds() / 86400 if p else 0.0
            status, ci = classify(v["obs"], p["status"] if p else None, days)
            mean = statistics.fmean(v["obs"]) if v["obs"] else None
            since = p["since"] if p and p["status"] == status else now.isoformat()
            c.execute("""INSERT INTO theme_gate(theme_id,kind,status,n_calls,n_independent,mean_excess,ci_lo,ci_hi,since,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(theme_id,kind) DO UPDATE SET status=excluded.status,n_calls=excluded.n_calls,n_independent=excluded.n_independent,mean_excess=excluded.mean_excess,
                         ci_lo=excluded.ci_lo,ci_hi=excluded.ci_hi,since=excluded.since,updated_at=excluded.updated_at""",
                      (theme, kind, status, v["n_calls"], len(v["obs"]), mean, ci[0] if ci else None, ci[1] if ci else None, since, now.isoformat()))
            if (p["status"] if p else None) != status:
                reason = (f"upper {int(DEMOTE_CONF * 100)}% bound {ci[1]:+.2f}% <= 0 over {len(v['obs'])} independent observations: clearly not beating the benchmark" if status == "poor" else
                          f"left 'poor': upper bound {ci[1]:+.2f}% now above the {REENABLE_MARGIN:+.2f}% re-enable margin" if p and p["status"] == "poor" else
                          f"{len(v['obs'])} independent observations; interval {ci}" if ci else f"{len(v['obs'])} of {THEME_MIN_N} independent observations")
                c.execute("INSERT INTO theme_gate_log(at,theme_id,kind,old_status,new_status,reason,stats_json) VALUES(?,?,?,?,?,?,?)",
                          (now.isoformat(), theme, kind, p["status"] if p else None, status, reason, json.dumps({"n_calls": v["n_calls"], "n_independent": len(v["obs"]), "mean": mean, "ci": ci})))
                changes.append({"theme": theme, "kind": kind, "from": p["status"] if p else None, "to": status, "reason": reason})
    return changes


def demoted_themes(store: Store, kind: str) -> frozenset:
    with store.conn() as c:
        return frozenset(r["theme_id"] for r in c.execute("SELECT theme_id FROM theme_gate WHERE kind=? AND status='poor'", (kind,)).fetchall())


def apply_theme_demotions(rows: list, demoted) -> list:
    """BUY/SELL driven mainly by a demoted theme is shown as WATCH. The original call is KEPT in news_action, so it is still
    logged as a shadow call: the evidence keeps accumulating and the theme can earn its way back."""
    for r in rows:
        if demoted and r.get("action") in ("BUY", "SELL") and r.get("primary_theme") in demoted:
            r.setdefault("news_action", r["action"])
            r["action"], r["demoted_theme"] = "WATCH", True
            if "why" in r:
                r["why"] = f"{r.get('why') or ''} [theme demoted: its calls have clearly not beaten the market]".strip()
    return rows


def theme_health(store: Store, labels: dict | None = None) -> dict:
    with store.conn() as c:
        gate = [dict(r) for r in c.execute("SELECT * FROM theme_gate ORDER BY CASE status WHEN 'poor' THEN 0 WHEN 'unproven' THEN 1 WHEN 'building' THEN 2 ELSE 3 END, theme_id").fetchall()]
        log = [dict(r) for r in c.execute("SELECT at,theme_id,kind,old_status,new_status,reason FROM theme_gate_log ORDER BY id DESC LIMIT 20").fetchall()]
    for g in gate:
        g["label"] = (labels or {}).get(g["theme_id"], g["theme_id"])
    return {"min_independent": THEME_MIN_N, "confidence": DEMOTE_CONF, "window_days": THEME_WINDOW_DAYS, "themes": gate, "log": log,
            "rule": f"Demoted only when the upper {int(DEMOTE_CONF * 100)}% bound of average excess return is <= 0 over at least {THEME_MIN_N} independent theme-days in the last {THEME_WINDOW_DAYS} days."}
