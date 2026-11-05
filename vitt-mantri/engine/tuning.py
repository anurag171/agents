"""Re-tuning the strength weights WITHOUT fooling ourselves.

The risk: with a few hundred calls you can always find weights that look great on the data you searched. The protocol:
  1. SPLIT BY TIME, never at random: oldest 60% of days = train, next 20% = validation, newest 20% = test. A day's calls stay together.
  2. PURGE: calls whose outcome window reaches into the next split are dropped, so overlapping market moves can't leak across the boundary.
  3. SEARCH on train only; pick among the top candidates on validation only. The test split is not even looked at until a single candidate remains.
  4. ONE LOOK at test: improvement over the CURRENT weights must be positive, and a paired bootstrap over days must clear zero at 97.5% (rule calibrated by simulation: see tests and the report).
  5. BURNED TEST DATA: every run is recorded; a later run whose test window overlaps an earlier one is flagged and cannot be adopted without --allow-reuse.
  6. NOTHING IS AUTOMATIC: `report` only reports. `adopt` writes config/scoring_weights.json and bumps the scoring version, which restarts
     the track record for the new formula (the old calls were used to choose it, so they can't also be its evidence).
Weights are bounded to 5-50% each so no single component can take over, and the objective is rank correlation between strength and the
signed return, i.e. exactly what the Track record tab reports."""
from __future__ import annotations

import json
import math
import os
import random
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone

from . import scoring
from .signal_log import HEADLINE_H, spearman
from .store import Store

MIN_CALLS, MIN_DAYS = 300, 60
BOUNDS = (0.05, 0.50)
TRAIN_FRAC, VAL_FRAC = 0.60, 0.20
N_CAND, TOP_K, MIN_VAL_GAIN, BOOT = 2000, 5, 0.02, 1000
BOOT_Q = float(os.getenv("VM_TUNE_BOOT_Q", "0.025"))      # test-period improvement must clear zero at 97.5%; measured by simulation (see report notes)


def load_rows(store: Store, horizon: int = HEADLINE_H, kind: str = "stock") -> list:
    with store.conn() as c:
        raw = c.execute("""SELECT s.emitted_date d, s.breakdown_json b, o.signed_ret_pct y FROM signals_log s JOIN signal_outcomes o ON o.signal_id=s.id
                           WHERE o.horizon=? AND s.kind=? AND s.breakdown_json IS NOT NULL AND o.signed_ret_pct IS NOT NULL""", (horizon, kind)).fetchall()
    rows = []
    for r in raw:
        items = json.loads(r["b"] or "[]")
        if not items:
            continue
        comps = {i["key"]: (i["value_pct"] / 100 if i.get("available") and i.get("value_pct") is not None else None) for i in items}
        rows.append({"d": date.fromisoformat(r["d"]), "comps": comps, "y": r["y"]})
    return rows


def project(w: dict, lo: float = BOUNDS[0], hi: float = BOUNDS[1]) -> dict:
    v = dict(w)
    for _ in range(60):
        v = {k: min(hi, max(lo, x)) for k, x in v.items()}
        s = sum(v.values()); v = {k: x / s for k, x in v.items()}
        if all(lo - 1e-9 <= x <= hi + 1e-9 for x in v.values()):
            break
    return v


def _ic(rows: list, w: dict) -> float:
    if len(rows) < 20:
        return 0.0
    return spearman([scoring.pct_from_components(r["comps"], w)[0] for r in rows], [r["y"] for r in rows])


def split_by_time(rows: list, horizon: int):
    days = sorted({r["d"] for r in rows}); n = len(days)
    d_val, d_test = days[int(n * TRAIN_FRAC)], days[int(n * (TRAIN_FRAC + VAL_FRAC))]
    purge = timedelta(days=math.ceil(horizon * 1.5) + 2)
    train = [r for r in rows if r["d"] < d_val - purge]
    val = [r for r in rows if d_val <= r["d"] < d_test - purge]
    test = [r for r in rows if r["d"] >= d_test]
    rng = lambda xs: {"from": min(r["d"] for r in xs).isoformat(), "to": max(r["d"] for r in xs).isoformat(), "n": len(xs)} if xs else {"n": 0}
    return train, val, test, {"train": rng(train), "val": rng(val), "test": rng(test), "purge_days": purge.days}


def _paired_boot_deltas(test: list, base: dict, cand: dict, seed: int) -> list:
    by_day = {}
    for r in test:
        by_day.setdefault(r["d"], []).append(r)
    days, rnd = list(by_day), random.Random(seed)
    sb = {id(r): scoring.pct_from_components(r["comps"], base)[0] for r in test}
    sc = {id(r): scoring.pct_from_components(r["comps"], cand)[0] for r in test}
    deltas = []
    for _ in range(BOOT):
        pick = [r for d in rnd.choices(days, k=len(days)) for r in by_day[d]]
        y = [r["y"] for r in pick]
        deltas.append(spearman([sc[id(r)] for r in pick], y) - spearman([sb[id(r)] for r in pick], y))
    return sorted(deltas)


def tune(rows: list, base: dict | None = None, horizon: int = HEADLINE_H, seed: int = 7, n_cand: int = N_CAND) -> dict:
    base = dict(base or scoring.W)
    days = {r["d"] for r in rows}
    if len(rows) < MIN_CALLS or len(days) < MIN_DAYS:
        return {"decision": "INSUFFICIENT_DATA", "n_rows": len(rows), "n_days": len(days), "need": {"calls": MIN_CALLS, "days": MIN_DAYS},
                "message": f"Not enough settled calls to tune safely ({len(rows)} calls over {len(days)} days; need {MIN_CALLS} over {MIN_DAYS}). Keep the current weights."}
    train, val, test, info = split_by_time(rows, horizon)
    rnd = random.Random(seed)
    cands = [project({k: v * math.exp(rnd.gauss(0, 0.6)) for k, v in base.items()}) for _ in range(n_cand)]
    # PHASE 1 - search on TRAIN only
    top = sorted(cands, key=lambda w: -_ic(train, w))[:TOP_K]
    # PHASE 2 - choose on VALIDATION only
    best = max(top, key=lambda w: _ic(val, w))
    ic = {"train": {"base": _ic(train, base), "cand": _ic(train, best)}, "val": {"base": _ic(val, base), "cand": _ic(val, best)}}
    d_val = ic["val"]["cand"] - ic["val"]["base"]
    # PHASE 3 - the single look at TEST
    ic["test"] = {"base": _ic(test, base), "cand": _ic(test, best)}
    d_test = ic["test"]["cand"] - ic["test"]["base"]
    deltas = _paired_boot_deltas(test, base, best, seed)
    lo = deltas[int(BOOT_Q * len(deltas))]
    adopt = d_val >= MIN_VAL_GAIN and d_test > 0 and lo > 0
    notes = []
    if d_val >= MIN_VAL_GAIN and not adopt:
        notes.append("Candidate looked better on validation but did not hold up on the untouched test period: classic overfitting. Keep the current weights.")
    return {"decision": "ADOPT" if adopt else "KEEP", "horizon": horizon, "n_rows": len(rows), "n_days": len(days), "splits": info,
            "base_weights": {k: round(v, 4) for k, v in base.items()}, "candidate_weights": {k: round(v, 4) for k, v in best.items()},
            "ic": {s: {k: round(v, 4) for k, v in d.items()} for s, d in ic.items()}, "delta_val": round(d_val, 4), "delta_test": round(d_test, 4),
            "delta_test_lo": round(lo, 4), "boot_confidence": round(1 - BOOT_Q, 4), "notes": notes,
            "message": ("Candidate weights improved on validation AND on the untouched test period (paired bootstrap lower bound above zero at the configured confidence)." if adopt else
                        "Keep the current weights: the candidate did not clearly beat them on held-out data.")}


def run(store: Store, horizon: int = HEADLINE_H, seed: int = 7, now: datetime | None = None, n_cand: int = N_CAND) -> dict:
    rep = tune(load_rows(store, horizon), horizon=horizon, seed=seed, n_cand=n_cand)
    now = now or datetime.now(timezone.utc)
    if rep["decision"] == "INSUFFICIENT_DATA":
        rep["test_look"] = 0
        return rep
    t0, t1 = rep["splits"]["test"]["from"], rep["splits"]["test"]["to"]
    with store.conn() as c:
        prior = c.execute("SELECT COUNT(*) n FROM tuning_runs WHERE decision!='INSUFFICIENT_DATA' AND COALESCE(target,'news')='news' AND test_end>=? AND test_start<=?", (t0, t1)).fetchone()["n"]
        rep["test_look"] = prior + 1
        if prior:
            rep["notes"].append(f"This test window overlaps data already used by {prior} earlier run(s): the test result is now optimistic and the weights cannot be adopted without --allow-reuse.")
        cur = c.execute("""INSERT INTO tuning_runs(run_at,horizon,n_rows,train_range,val_range,test_range,test_start,test_end,base_json,cand_json,metrics_json,decision,test_look)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now.isoformat(), horizon, rep["n_rows"], json.dumps(rep["splits"]["train"]), json.dumps(rep["splits"]["val"]),
                           json.dumps(rep["splits"]["test"]), t0, t1, json.dumps(rep["base_weights"]), json.dumps(rep["candidate_weights"]),
                           json.dumps({k: rep[k] for k in ("ic", "delta_val", "delta_test", "delta_test_lo")}), rep["decision"], rep["test_look"]))
        rep["run_id"] = cur.lastrowid
    return rep


def next_version(v: str) -> str:
    m = re.match(r"^(\D*)(\d+)$", v)
    return f"{m.group(1)}{int(m.group(2)) + 1}" if m else v + "1"


def adopt(store: Store, run_id: int | None = None, allow_reuse: bool = False, path: str | None = None) -> dict:
    path = path or scoring.WEIGHTS_FILE
    with store.conn() as c:
        r = (c.execute("SELECT * FROM tuning_runs WHERE id=?", (run_id,)) if run_id else c.execute("SELECT * FROM tuning_runs ORDER BY id DESC LIMIT 1")).fetchone()
    if not r:
        raise ValueError("no tuning run to adopt; run `report` first")
    if (r["target"] or "news") != "news":
        raise ValueError(f"run {r['id']} is a {r['target']} tuning run; use engine.screen_tuning to adopt it")
    if r["decision"] != "ADOPT":
        raise ValueError(f"run {r['id']} decided {r['decision']}: nothing to adopt")
    if r["test_look"] > 1 and not allow_reuse:
        raise ValueError(f"run {r['id']} re-used a test window ({r['test_look']} looks); its result is optimistic. Pass allow_reuse to override.")
    w = project(json.loads(r["cand_json"]))
    ver = next_version(scoring.SCORING_VERSION)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"version": ver, "weights": {k: round(v, 6) for k, v in w.items()}, "from_run": r["id"], "adopted_at": datetime.now(timezone.utc).isoformat(),
                   "note": "Chosen on train/validation, confirmed once on a held-out test period. Restart the scheduler to apply; the track record restarts for this version."}, fh, indent=1)
    os.replace(tmp, path)
    with store.conn() as c:
        c.execute("UPDATE tuning_runs SET adopted=1 WHERE id=?", (r["id"],))
    return {"version": ver, "weights": w, "path": path, "run_id": r["id"]}


def rollback(path: str | None = None) -> bool:
    path = path or scoring.WEIGHTS_FILE
    if os.path.exists(path):
        os.remove(path); return True
    return False


def _print(rep: dict):
    print(f"\nDecision: {rep['decision']}  -  {rep['message']}")
    if rep["decision"] == "INSUFFICIENT_DATA":
        return
    print(f"Calls: {rep['n_rows']} over {rep['n_days']} days | splits: " + " | ".join(f"{k} {v.get('from', '-')}..{v.get('to', '-')} (n={v['n']})" for k, v in rep["splits"].items() if k != "purge_days") + f" | purge {rep['splits']['purge_days']}d")
    print("Rank correlation of strength vs return:  " + "  ".join(f"{s}: base {v['base']:+.3f} -> cand {v['cand']:+.3f}" for s, v in rep["ic"].items()))
    print(f"Gain on validation {rep['delta_val']:+.3f} | gain on untouched test {rep['delta_test']:+.3f} ({rep['boot_confidence']:.1%} lower bound {rep['delta_test_lo']:+.3f}) | test looks: {rep.get('test_look')}")
    print("Weights:  " + "  ".join(f"{k} {rep['base_weights'][k]:.2f}->{rep['candidate_weights'][k]:.2f}" for k in rep["base_weights"]))
    for n in rep["notes"]:
        print("NOTE:", n)


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "report"), sys.argv[2:]
    h = int(args[args.index("--horizon") + 1]) if "--horizon" in args else HEADLINE_H
    st = Store()
    if cmd == "report":
        _print(run(st, h))
    elif cmd == "adopt":
        out = adopt(st, allow_reuse="--allow-reuse" in args)
        print(f"Adopted weights as version {out['version']} -> {out['path']}. Restart the scheduler to apply.")
    elif cmd == "rollback":
        print("rolled back to default weights" if rollback() else "no adopted weights file")
    else:
        print("usage: python -m engine.tuning [report|adopt|rollback] [--horizon 5] [--allow-reuse]")
