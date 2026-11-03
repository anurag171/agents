import json, random
from datetime import date, datetime, timedelta, timezone
import pytest
from engine import scoring, tuning as T
from engine.store import Store

KEYS = list(scoring.DEFAULT_W)
D0 = date(2026, 1, 5)


def make_rows(n_days=150, per_day=6, seed=1, signal=None, noise=2.0):
    """signal = {component: slope}; y = sum(slope * (value-0.5)) + noise. signal=None -> pure noise (no real edge anywhere)."""
    rnd, rows = random.Random(seed), []
    for i in range(n_days):
        for _ in range(per_day):
            comps = {k: (None if k in ("chart", "market") and rnd.random() < 0.15 else rnd.random()) for k in KEYS}
            y = rnd.gauss(0, noise) + sum(s * ((comps[k] if comps[k] is not None else 0.5) - 0.5) for k, s in (signal or {}).items())
            rows.append({"d": D0 + timedelta(days=i), "comps": comps, "y": y})
    return rows


def to_db(st, rows):
    with st.conn() as c:
        for i, r in enumerate(rows):
            bd = [{"key": k, "available": v is not None, "value_pct": None if v is None else int(round(v * 100))} for k, v in r["comps"].items()]
            cur = c.execute("INSERT INTO signals_log(emitted_at,emitted_date,kind,ticker,direction,news_action,final_action,scoring_version,breakdown_json) VALUES(?,?,?,?,?,?,?,?,?)",
                            (r["d"].isoformat() + "T10:00:00+00:00", r["d"].isoformat(), "stock", f"T{i}", 1, "BUY", "BUY", "s1", json.dumps(bd)))
            c.execute("INSERT INTO signal_outcomes(signal_id,horizon,signed_ret_pct) VALUES(?,?,?)", (cur.lastrowid, 5, r["y"]))


@pytest.fixture
def st(tmp_path): return Store(str(tmp_path / "t.sqlite"))


def test_offline_formula_matches_production_scoring():
    row = {"net": 4.0, "buy_score": 5.0, "sell_score": 1.0, "sources": ["Reuters", "Moneycontrol"], "events": 2, "best_grade": "A",
           "chart": {"verdict": "CONFIRM", "score": 0.5, "reasons": ["r"]}, "market": {"change_pct": 1.4, "mkt": "IN"}}
    scored = scoring.score_row(row)
    comps = {b["key"]: (b["value_pct"] / 100 if b["available"] else None) for b in scored["signal_breakdown"]}
    assert abs(scoring.pct_from_components(comps)[0] - scored["score_pct"]) <= 1.0          # same maths live and in tuning (up to rounding)


def test_project_keeps_weights_inside_bounds_and_summing_to_one():
    rnd = random.Random(3)
    for _ in range(200):
        w = T.project({k: rnd.random() ** 3 + 1e-6 for k in KEYS})
        assert sum(w.values()) == pytest.approx(1.0) and all(T.BOUNDS[0] - 1e-6 <= v <= T.BOUNDS[1] + 1e-6 for v in w.values())


def test_finds_a_real_edge_and_confirms_it_on_untouched_data():
    rep = T.tune(make_rows(seed=1, signal={"chart": 5.0, "news": 1.0, "market": 0.0}), seed=7, n_cand=250)
    assert rep["decision"] == "ADOPT", rep
    assert rep["candidate_weights"]["chart"] > rep["base_weights"]["chart"]                  # moved toward the component that really matters
    assert rep["delta_val"] >= T.MIN_VAL_GAIN and rep["delta_test"] > 0 and rep["delta_test_lo"] > 0
    assert all(T.BOUNDS[0] - 1e-6 <= v <= T.BOUNDS[1] + 1e-6 for v in rep["candidate_weights"].values())


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_pure_noise_never_gets_adopted(seed):
    rep = T.tune(make_rows(seed=seed, signal=None), seed=7, n_cand=250)
    assert rep["decision"] == "KEEP" and rep["decision"] != "ADOPT"                           # no edge exists, so nothing may pass held-out data


def test_search_never_sees_the_test_period_and_test_is_looked_at_once(monkeypatch):
    calls, real = [], T._ic
    def spy(rows, w):
        calls.append((min(r["d"] for r in rows), max(r["d"] for r in rows)) if rows else None); return real(rows, w)
    monkeypatch.setattr(T, "_ic", spy)
    rep = T.tune(make_rows(seed=2, signal={"chart": 4.0}), seed=7, n_cand=250)
    t0 = date.fromisoformat(rep["splits"]["test"]["from"])
    seen_test = [i for i, c in enumerate(calls) if c and c[1] >= t0]
    assert seen_test == [len(calls) - 2, len(calls) - 1]                                       # only the final base+candidate comparison touched test
    assert all(c[1] < t0 for c in calls[:-2])


def test_splits_are_time_ordered_and_purged():
    rows = make_rows(seed=3, signal=None)
    tr, va, te, info = T.split_by_time(rows, 5)
    assert max(r["d"] for r in tr) < min(r["d"] for r in va) and max(r["d"] for r in va) < min(r["d"] for r in te)
    gap1 = (min(r["d"] for r in va) - max(r["d"] for r in tr)).days; gap2 = (min(r["d"] for r in te) - max(r["d"] for r in va)).days
    assert gap1 >= info["purge_days"] and gap2 >= info["purge_days"] and info["purge_days"] >= 8
    assert len(tr) + len(va) + len(te) < len(rows)                                             # boundary rows were purged, not reused


def test_too_little_data_refuses_to_tune(st):
    rep = T.run(st, 5, n_cand=250)
    assert rep["decision"] == "INSUFFICIENT_DATA" and rep["test_look"] == 0 and "Keep the current weights" in rep["message"]
    to_db(st, make_rows(n_days=20, per_day=5)); assert T.run(st, 5, n_cand=250)["decision"] == "INSUFFICIENT_DATA"
    with st.conn() as c: assert c.execute("SELECT COUNT(*) n FROM tuning_runs").fetchone()["n"] == 0


def test_burned_test_window_is_flagged_and_blocks_adoption(st, tmp_path, monkeypatch):
    to_db(st, make_rows(seed=1, signal={"chart": 5.0, "news": 1.0}))
    first = T.run(st, 5, n_cand=250); second = T.run(st, 5, n_cand=250)
    assert first["decision"] == "ADOPT" and first["test_look"] == 1 and second["test_look"] == 2 and any("optimistic" in n for n in second["notes"])
    path = str(tmp_path / "w.json")
    with pytest.raises(ValueError, match="re-used a test window"): T.adopt(st, second["run_id"], path=path)
    out = T.adopt(st, second["run_id"], allow_reuse=True, path=path); assert out["version"] == "s2"


def test_adopt_writes_versioned_weights_scoring_loads_them_and_rollback_restores(st, tmp_path, monkeypatch):
    to_db(st, make_rows(seed=1, signal={"chart": 5.0, "news": 1.0})); rep = T.run(st, 5, n_cand=250); path = str(tmp_path / "scoring_weights.json")
    out = T.adopt(st, rep["run_id"], path=path)
    d = json.load(open(path)); assert d["version"] == "s2" and sum(d["weights"].values()) == pytest.approx(1.0) and d["from_run"] == rep["run_id"]
    monkeypatch.setattr(scoring, "WEIGHTS_FILE", path)
    w, v = scoring._load_weights(); assert v == "s2" and w["chart"] == pytest.approx(out["weights"]["chart"], abs=1e-5) and w != scoring.DEFAULT_W
    with st.conn() as c: assert c.execute("SELECT adopted FROM tuning_runs WHERE id=?", (rep["run_id"],)).fetchone()["adopted"] == 1
    assert T.rollback(path) is True and scoring._load_weights() == (scoring.DEFAULT_W, "s1") and T.rollback(path) is False


def test_nothing_to_adopt_when_decision_is_keep_and_bad_weight_files_are_ignored(st, tmp_path, monkeypatch):
    to_db(st, make_rows(seed=4, signal=None)); rep = T.run(st, 5, n_cand=250)
    assert rep["decision"] == "KEEP"
    with pytest.raises(ValueError, match="nothing to adopt"): T.adopt(st, rep["run_id"], path=str(tmp_path / "x.json"))
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps({"version": "s9", "weights": {"news": 1.0}}))
    monkeypatch.setattr(scoring, "WEIGHTS_FILE", str(bad)); assert scoring._load_weights() == (scoring.DEFAULT_W, "s1")      # corrupt/partial file never applied
    assert T.next_version("s1") == "s2" and T.next_version("s9") == "s10"
