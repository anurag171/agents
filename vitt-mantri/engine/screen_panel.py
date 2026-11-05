"""Panel of EVERY screened stock (not just the picks), settled against what happened next.

Why: tuning the screen weights on picks alone is biased - picks already passed the thresholds, so the data only covers the top of the
distribution. To learn whether a factor ranks stocks better than another we need the whole cross-section on each date: every stock's factor
scores at that time, and its excess return over the benchmark afterwards.

Rules: snapshot at most once every PANEL_EVERY_DAYS (overlapping weekly snapshots would be near-copies); only when most stocks have
fundamentals (price-only snapshots would mislead a fundamentals-factor study); entry = the price used at snapshot time, exit = the close
h trading days AFTER the snapshot date (same no-look-ahead rule as the signals log). Point-in-time fundamentals cannot be rebuilt
historically, so there is NO backfill: the panel only grows by running forward."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from .bars import fetch_bars, yahoo_symbol
from .signal_log import BENCH, compute_outcome
from .store import Store

PANEL_EVERY_DAYS, MIN_FUND_RATIO, HORIZONS = 7, 0.60, (20, 60)


def record_panel(store: Store, panel: list, version: str, with_fundamentals: int, eligible: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc); today = now.date()
    if eligible <= 0 or with_fundamentals / eligible < MIN_FUND_RATIO:
        return {"logged": 0, "reason": f"fundamentals coverage {with_fundamentals}/{eligible} is below {MIN_FUND_RATIO:.0%}"}
    with store.conn() as c:
        last = c.execute("SELECT MAX(run_date) d FROM screen_panel WHERE version=?", (version,)).fetchone()["d"]
        if last and (today - date.fromisoformat(last)).days < PANEL_EVERY_DAYS:
            return {"logged": 0, "reason": f"last snapshot {last} is less than {PANEL_EVERY_DAYS} days old"}
        n = 0
        for r in panel:
            n += c.execute("INSERT OR IGNORE INTO screen_panel(run_date,ticker,version,yahoo,price,score_pct,tier,coverage_pct,comps_json) VALUES(?,?,?,?,?,?,?,?,?)",
                           (today.isoformat(), r["ticker"], version, yahoo_symbol(r["ticker"], "IN"), r["price"], r["score"], None, int(round(r["coverage"] * 100)), json.dumps(r["comps"]))).rowcount
    return {"logged": n, "run_date": today.isoformat()}


def evaluate_panel(store: Store, fetch=fetch_bars, today: date | None = None, max_symbols: int = 700) -> dict:
    today = today or datetime.now(timezone.utc).date()
    with store.conn() as c:
        todo = c.execute("""SELECT p.run_date, p.ticker, p.version, p.yahoo, p.price, h.h horizon FROM screen_panel p JOIN (SELECT 20 h UNION SELECT 60) h
                 LEFT JOIN screen_panel_outcomes o ON o.run_date=p.run_date AND o.ticker=p.ticker AND o.version=p.version AND o.horizon=h.h
                 WHERE o.run_date IS NULL""").fetchall()
    due = [t for t in todo if (today - date.fromisoformat(t["run_date"])).days >= t["horizon"]]
    by_sym, cache, settled = {}, {}, 0
    for t in due:
        by_sym.setdefault(t["yahoo"], []).append(t)
    def bars(sym):
        if sym not in cache:
            d = fetch(sym, rng="2y"); cache[sym] = d["bars"] if d else None
        return cache[sym]
    bench = bars(BENCH["IN"]) if due else None
    for sym in list(by_sym)[:max_symbols]:
        b = bars(sym)
        if not b:
            continue
        for t in by_sym[sym]:
            o = compute_outcome(b, date.fromisoformat(t["run_date"]), +1, t["price"], t["horizon"], bench)
            if not o or o["signed_excess_pct"] is None:
                continue
            with store.conn() as c:
                c.execute("INSERT OR IGNORE INTO screen_panel_outcomes(run_date,ticker,version,horizon,exit_date,ret_pct,bench_ret_pct,excess_pct,settled_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (t["run_date"], t["ticker"], t["version"], t["horizon"], o["exit_date"], o["ret_pct"], o["bench_ret_pct"], o["signed_excess_pct"], datetime.now(timezone.utc).isoformat()))
            settled += 1
    return {"due": len(due), "settled": settled}


def load_panel(store: Store, horizon: int, version: str | None = None) -> dict:
    """-> {run_date: [{'comps':{..}, 'y': excess return in PERCENT}]} for settled rows."""
    q = """SELECT p.run_date d, p.comps_json c, o.excess_pct y FROM screen_panel p JOIN screen_panel_outcomes o
           ON o.run_date=p.run_date AND o.ticker=p.ticker AND o.version=p.version WHERE o.horizon=?""" + (" AND p.version=?" if version else "")
    with store.conn() as c:
        rows = c.execute(q, (horizon,) + ((version,) if version else ())).fetchall()
    out = {}
    for r in rows:
        out.setdefault(date.fromisoformat(r["d"]), []).append({"comps": json.loads(r["c"]), "y": r["y"]})
    return out
