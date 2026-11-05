"""Held-out tuning of the SCREEN's factor weights (quality / growth / value / momentum / stability).

Question asked on every snapshot date: does the composite rank the screened stocks by their LATER excess return over the benchmark?
Objective = mean cross-sectional rank correlation across dates. Same discipline as engine/tuning.py, adapted to a panel:
  * only THINNED dates are used (spaced >= the settling horizon), so no two dates share return windows: each date is ~one independent observation;
  * time-ordered 60/20/20 split over those dates; search on train only, choose on validation only, ONE look at test;
  * adoption needs a positive gain on validation AND on the untouched test dates, >= 8 test dates, the gain present on >= 75% of them, and a paired bootstrap over test DATES clearing zero at 97.5%;
  * used test windows are recorded; re-using one blocks adoption without --allow-reuse;
  * nothing is automatic: `adopt` writes config/screen_weights.json (version bump q1 -> q2) and the new version starts a fresh track record.
THRESHOLDS (PICK_MIN etc.) are deliberately NOT tuned: with a few dozen independent dates there is no honest way to fit them.
Expect "insufficient data" for a long time: at a 20-trading-day horizon, 40 independent dates is about three and a half years of forward running. Point-in-time
fundamentals cannot be rebuilt historically, so the panel cannot be backfilled; do not try to."""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys
from datetime import date, datetime, timedelta, timezone

from . import scoring, screener
from .screen_panel import load_panel
from .signal_log import spearman
from .store import Store
from .tuning import next_version, project

HORIZON, MIN_DATES, MIN_PER_DATE = 20, 40, 30
MIN_TEST_DATES, MIN_CONSISTENCY = 8, 0.75                  # a bootstrap over a handful of dates is unreliable: need >= 8 test dates and a consistent gain
TRAIN_FRAC, VAL_FRAC = 0.60, 0.20
N_CAND, TOP_K, MIN_VAL_GAIN, BOOT = 1500, 5, 0.01, 1000
BOOT_Q = float(os.getenv("VM_TUNE_BOOT_Q", "0.025"))
BOUNDS = (0.05, 0.50)


def thin(panel: dict, spacing_days: int) -> dict:
    """Keep the earliest date, then only dates >= spacing_days after the last kept one; drop dates with too few stocks."""
    kept, last = {}, None
    for d in sorted(panel):
        if len(panel[d]) < MIN_PER_DATE:
            continue
        if last is None or (d - last).days >= spacing_days:
            kept[d] = panel[d]; last = d
    return kept


def _ic_date(items: list, w: dict) -> float:
    return spearman([scoring.pct_from_components(r["comps"], w)[0] for r in items], [r["y"] for r in items])


def _mean_ic(panel: dict, dates: list, w: dict) -> float:
    return statistics.fmean(_ic_date(panel[d], w) for d in dates) if dates else 0.0


def split_dates(dates: list):
    n = len(dates); i1, i2 = int(n * TRAIN_FRAC), int(n * (TRAIN_FRAC + VAL_FRAC))
    return dates[:i1], dates[i1:i2], dates[i2:]


def tune(panel: dict, base: dict | None = None, horizon: int = HORIZON, seed: int = 7, n_cand: int = N_CAND) -> dict:
    base = dict(base or screener.W)
    spacing = math.ceil(horizon * 1.45)                       # ~calendar days for `horizon` trading days: return windows cannot overlap
    ind = thin(panel, spacing); dates = sorted(ind)
    if len(dates) < MIN_DATES:
        return {"decision": "INSUFFICIENT_DATA", "n_dates": len(dates), "need": {"independent_dates": MIN_DATES, "stocks_per_date": MIN_PER_DATE},
                "message": f"Not enough independent snapshot dates to tune safely ({len(dates)} of {MIN_DATES}, spaced {spacing}+ days apart). Keep the current weights."}
    tr, va, te = split_dates(dates)
    rnd = random.Random(seed)
    cands = [project({k: v * math.exp(rnd.gauss(0, 0.6)) for k, v in base.items()}, *BOUNDS) for _ in range(n_cand)]
    top = sorted(cands, key=lambda w: -_mean_ic(ind, tr, w))[:TOP_K]                     # PHASE 1: search on TRAIN dates only
    best = max(top, key=lambda w: _mean_ic(ind, va, w))                                  # PHASE 2: choose on VALIDATION dates only
    ic = {"train": {"base": _mean_ic(ind, tr, base), "cand": _mean_ic(ind, tr, best)}, "val": {"base": _mean_ic(ind, va, base), "cand": _mean_ic(ind, va, best)}}
    d_val = ic["val"]["cand"] - ic["val"]["base"]
    ic["test"] = {"base": _mean_ic(ind, te, base), "cand": _mean_ic(ind, te, best)}      # PHASE 3: the single look at TEST dates
    d_test = ic["test"]["cand"] - ic["test"]["base"]
    per = [(_ic_date(ind[d], best) - _ic_date(ind[d], base)) for d in te]
    boots = sorted(statistics.fmean(rnd.choices(per, k=len(per))) for _ in range(BOOT))
    lo = boots[int(BOOT_Q * len(boots))]
    consistent = sum(1 for x in per if x > 0) / len(per) if per else 0.0
    adopt = d_val >= MIN_VAL_GAIN and d_test > 0 and lo > 0 and len(te) >= MIN_TEST_DATES and consistent >= MIN_CONSISTENCY
    notes = []
    if d_val >= MIN_VAL_GAIN and not adopt:
        notes.append("Candidate looked better on validation but not on the untouched test dates: classic overfitting. Keep the current weights.")
    rng = lambda xs: {"from": xs[0].isoformat(), "to": xs[-1].isoformat(), "n": len(xs)} if xs else {"n": 0}
    return {"decision": "ADOPT" if adopt else "KEEP", "horizon": horizon, "n_dates": len(dates), "spacing_days": spacing, "splits": {"train": rng(tr), "val": rng(va), "test": rng(te)},
            "base_weights": {k: round(v, 4) for k, v in base.items()}, "candidate_weights": {k: round(v, 4) for k, v in best.items()},
            "ic": {s: {k: round(v, 4) for k, v in d.items()} for s, d in ic.items()}, "delta_val": round(d_val, 4), "delta_test": round(d_test, 4),
            "delta_test_lo": round(lo, 4), "test_dates_improved": round(consistent, 3), "boot_confidence": round(1 - BOOT_Q, 4), "notes": notes,
            "message": ("Candidate weights improved cross-sectional ranking on validation AND on untouched test dates (bootstrap over dates clears zero)." if adopt else
                        "Keep the current weights: the candidate did not clearly beat them on held-out dates.")}


def run(store: Store, horizon: int = HORIZON, seed: int = 7, now: datetime | None = None, n_cand: int = N_CAND) -> dict:
    rep = tune(load_panel(store, horizon), horizon=horizon, seed=seed, n_cand=n_cand)
    now = now or datetime.now(timezone.utc)
    if rep["decision"] == "INSUFFICIENT_DATA":
        rep["test_look"] = 0; return rep
    t0, t1 = rep["splits"]["test"]["from"], rep["splits"]["test"]["to"]
    with store.conn() as c:
        prior = c.execute("SELECT COUNT(*) n FROM tuning_runs WHERE target='screen' AND decision!='INSUFFICIENT_DATA' AND test_end>=? AND test_start<=?", (t0, t1)).fetchone()["n"]
        rep["test_look"] = prior + 1
        if prior:
            rep["notes"].append(f"This test window overlaps dates already used by {prior} earlier run(s): the test result is now optimistic and the weights cannot be adopted without --allow-reuse.")
        cur = c.execute("""INSERT INTO tuning_runs(run_at,horizon,n_rows,train_range,val_range,test_range,test_start,test_end,base_json,cand_json,metrics_json,decision,test_look,target)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'screen')""", (now.isoformat(), horizon, rep["n_dates"], json.dumps(rep["splits"]["train"]), json.dumps(rep["splits"]["val"]),
                           json.dumps(rep["splits"]["test"]), t0, t1, json.dumps(rep["base_weights"]), json.dumps(rep["candidate_weights"]),
                           json.dumps({k: rep[k] for k in ("ic", "delta_val", "delta_test", "delta_test_lo")}), rep["decision"], rep["test_look"]))
        rep["run_id"] = cur.lastrowid
    return rep


def adopt(store: Store, run_id: int | None = None, allow_reuse: bool = False, path: str | None = None) -> dict:
    path = path or screener.WEIGHTS_FILE
    with store.conn() as c:
        r = (c.execute("SELECT * FROM tuning_runs WHERE id=? AND target='screen'", (run_id,)) if run_id else c.execute("SELECT * FROM tuning_runs WHERE target='screen' ORDER BY id DESC LIMIT 1")).fetchone()
    if not r:
        raise ValueError("no screen tuning run to adopt; run `report` first")
    if r["decision"] != "ADOPT":
        raise ValueError(f"run {r['id']} decided {r['decision']}: nothing to adopt")
    if r["test_look"] > 1 and not allow_reuse:
        raise ValueError(f"run {r['id']} re-used a test window ({r['test_look']} looks); its result is optimistic. Pass allow_reuse to override.")
    w, ver = project(json.loads(r["cand_json"]), *BOUNDS), next_version(screener.SCREEN_VERSION)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"version": ver, "weights": {k: round(v, 6) for k, v in w.items()}, "from_run": r["id"], "adopted_at": datetime.now(timezone.utc).isoformat(),
                   "note": "Chosen on train, selected on validation, confirmed once on held-out dates. Restart the scheduler to apply; screen picks start a fresh track record for this version."}, fh, indent=1)
    os.replace(tmp, path)
    with store.conn() as c:
        c.execute("UPDATE tuning_runs SET adopted=1 WHERE id=?", (r["id"],))
    return {"version": ver, "weights": w, "path": path, "run_id": r["id"]}


def rollback(path: str | None = None) -> bool:
    path = path or screener.WEIGHTS_FILE
    if os.path.exists(path):
        os.remove(path); return True
    return False


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "report"), sys.argv[2:]
    st = Store()
    if cmd == "report":
        rep = run(st)
        print(f"\nDecision: {rep['decision']}  -  {rep['message']}")
        if rep["decision"] != "INSUFFICIENT_DATA":
            print("Mean cross-sectional rank correlation: " + "  ".join(f"{s}: base {v['base']:+.3f} -> cand {v['cand']:+.3f}" for s, v in rep["ic"].items()))
            print(f"Gain on validation {rep['delta_val']:+.3f} | untouched test {rep['delta_test']:+.3f} ({rep['boot_confidence']:.1%} lower bound {rep['delta_test_lo']:+.3f}) | test looks: {rep['test_look']}")
            print("Weights:  " + "  ".join(f"{k} {rep['base_weights'][k]:.2f}->{rep['candidate_weights'][k]:.2f}" for k in rep["base_weights"]))
            for n in rep["notes"]:
                print("NOTE:", n)
    elif cmd == "adopt":
        out = adopt(st, allow_reuse="--allow-reuse" in args); print(f"Adopted screen weights as version {out['version']} -> {out['path']}. Restart the scheduler to apply.")
    elif cmd == "rollback":
        print("rolled back to default screen weights" if rollback() else "no adopted screen weights file")
    else:
        print("usage: python -m engine.screen_tuning [report|adopt|rollback] [--allow-reuse]")
