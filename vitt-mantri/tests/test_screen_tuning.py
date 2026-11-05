import json, random
from datetime import date, datetime, timedelta, timezone
import pytest
from engine import screen_panel as SP, screen_tuning as ST, screener as SC
from engine.store import Store
from engine.technicals import Bar

KEYS = list(SC.DEFAULT_W)
D0 = date(2024, 1, 5)


def panel_rows(n_stocks=60, rnd=None, slope=None, noise=4.0):
    rnd = rnd or random.Random(1); out = []
    for _ in range(n_stocks):
        comps = {k: (None if k in ("quality", "growth", "value") and rnd.random() < 0.1 else rnd.random()) for k in KEYS}
        y = rnd.gauss(0, noise) + sum(s * ((comps[k] if comps[k] is not None else 0.5) - 0.5) for k, s in (slope or {}).items())
        out.append({"comps": comps, "y": y})
    return out


def make_panel(n_dates=240, step=7, seed=1, slope=None, n_stocks=60):
    """Weekly snapshot dates (so thinning to ~29-day spacing leaves ~1/4 of them)."""
    rnd = random.Random(seed)
    return {D0 + timedelta(days=step * i): panel_rows(n_stocks, rnd, slope) for i in range(n_dates)}


def test_thinning_keeps_return_windows_apart_and_drops_small_dates():
    p = make_panel(40); p[D0 + timedelta(days=7)] = p[D0 + timedelta(days=7)][:5]               # a date with only 5 stocks
    t = ST.thin(p, 29); ds = sorted(t)
    assert all((b - a).days >= 29 for a, b in zip(ds, ds[1:])) and D0 + timedelta(days=7) not in t and len(ds) < len(p) / 3


def test_finds_a_real_factor_edge_and_confirms_it_on_untouched_dates():
    rep = ST.tune(make_panel(seed=3, slope={"momentum": 10.0, "quality": 4.0}), seed=7, n_cand=200)
    assert rep["decision"] == "ADOPT", rep
    assert rep["test_dates_improved"] >= ST.MIN_CONSISTENCY and rep["splits"]["test"]["n"] >= ST.MIN_TEST_DATES
    assert rep["candidate_weights"]["momentum"] > rep["base_weights"]["momentum"] and rep["delta_val"] >= ST.MIN_VAL_GAIN and rep["delta_test"] > 0 and rep["delta_test_lo"] > 0
    assert all(ST.BOUNDS[0] - 1e-6 <= v <= ST.BOUNDS[1] + 1e-6 for v in rep["candidate_weights"].values())


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_pure_noise_is_not_adopted(seed):
    assert ST.tune(make_panel(seed=seed, slope=None), seed=7, n_cand=200)["decision"] == "KEEP"


def test_insufficient_dates_refuses_and_says_how_many_are_needed():
    rep = ST.tune(make_panel(n_dates=150), seed=7, n_cand=50)
    assert rep["decision"] == "INSUFFICIENT_DATA" and rep["n_dates"] < ST.MIN_DATES and "Keep the current weights" in rep["message"]


def test_splits_are_time_ordered_and_test_dates_are_looked_at_once(monkeypatch):
    seen, real = [], ST._mean_ic
    def spy(panel, dates, w):
        seen.append((min(dates), max(dates)) if dates else None); return real(panel, dates, w)
    monkeypatch.setattr(ST, "_mean_ic", spy)
    rep = ST.tune(make_panel(seed=2, slope={"momentum": 8.0}), seed=7, n_cand=100)
    t0 = date.fromisoformat(rep["splits"]["test"]["from"]); tr, va, te = rep["splits"]["train"], rep["splits"]["val"], rep["splits"]["test"]
    assert tr["to"] < va["from"] and va["to"] < te["from"]
    touched = [i for i, c in enumerate(seen) if c and c[1] >= t0]
    assert touched == [len(seen) - 2, len(seen) - 1] and all(c[1] < t0 for c in seen[:-2])           # only the final base-vs-candidate comparison saw test dates


def seed_panel(st, panel, version="q1"):
    with st.conn() as c:
        for d, items in panel.items():
            for i, r in enumerate(items):
                c.execute("INSERT INTO screen_panel(run_date,ticker,version,yahoo,price,score_pct,coverage_pct,comps_json) VALUES(?,?,?,?,?,?,?,?)", (d.isoformat(), f"S{i}", version, f"S{i}.NS", 100.0, 50, 100, json.dumps(r["comps"])))
                c.execute("INSERT INTO screen_panel_outcomes(run_date,ticker,version,horizon,excess_pct) VALUES(?,?,?,?,?)", (d.isoformat(), f"S{i}", version, 20, r["y"]))


@pytest.fixture
def st(tmp_path): return Store(str(tmp_path / "t.sqlite"))


def test_burned_test_window_blocks_adoption_and_adopt_writes_versioned_weights(st, tmp_path, monkeypatch):
    seed_panel(st, make_panel(seed=3, slope={"momentum": 10.0, "quality": 4.0}))
    first = ST.run(st, n_cand=200); second = ST.run(st, n_cand=200); path = str(tmp_path / "screen_weights.json")
    assert first["decision"] == "ADOPT" and first["test_look"] == 1 and second["test_look"] == 2 and any("optimistic" in n for n in second["notes"])
    with pytest.raises(ValueError, match="re-used a test window"): ST.adopt(st, second["run_id"], path=path)
    out = ST.adopt(st, first["run_id"], path=path); d = json.load(open(path))
    assert out["version"] == "q2" and d["version"] == "q2" and sum(d["weights"].values()) == pytest.approx(1.0) and d["from_run"] == first["run_id"]
    monkeypatch.setattr(SC, "WEIGHTS_FILE", path); w, v = SC._load_weights()
    assert v == "q2" and w["momentum"] > SC.DEFAULT_W["momentum"]
    assert ST.rollback(path) is True and SC._load_weights() == (SC.DEFAULT_W, "q1")


def test_news_and_screen_tuning_runs_do_not_mix(st, tmp_path):
    from engine import tuning as NT
    seed_panel(st, make_panel(seed=3, slope={"momentum": 10.0}))
    rep = ST.run(st, n_cand=200)
    with pytest.raises(ValueError, match="screen tuning run"): NT.adopt(st, rep["run_id"], path=str(tmp_path / "x.json"))


def test_corrupt_or_partial_screen_weight_files_are_ignored(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps({"version": "q9", "weights": {"quality": 1.0}}))
    monkeypatch.setattr(SC, "WEIGHTS_FILE", str(bad)); assert SC._load_weights() == (SC.DEFAULT_W, "q1")
    bad.write_text("{not json"); assert SC._load_weights() == (SC.DEFAULT_W, "q1")


# ---------------------------------------------------------------- panel logging and settling
def test_panel_is_logged_weekly_only_with_enough_fundamentals_and_is_idempotent(st):
    rows = [{"ticker": f"S{i}", "price": 100.0 + i, "score": 60, "coverage": 1.0, "comps": {k: 0.5 for k in KEYS}} for i in range(5)]
    t0 = datetime(2026, 3, 2, 12, tzinfo=timezone.utc)
    assert SP.record_panel(st, rows, "q1", 30, 100, t0)["logged"] == 0                                    # 30% with fundamentals: a price-only snapshot would mislead
    assert SP.record_panel(st, rows, "q1", 80, 100, t0)["logged"] == 5
    assert SP.record_panel(st, rows, "q1", 80, 100, t0 + timedelta(days=3))["logged"] == 0                # not weekly yet
    assert SP.record_panel(st, rows, "q1", 80, 100, t0 + timedelta(days=8))["logged"] == 5
    assert SP.record_panel(st, rows, "q2", 80, 100, t0 + timedelta(days=9))["logged"] == 5                # a new weights version is a separate panel
    with st.conn() as c: assert c.execute("SELECT COUNT(DISTINCT run_date) n FROM screen_panel WHERE version='q1'").fetchone()["n"] == 2


def bars_from(start, closes):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5: d += timedelta(days=1)
        out.append(Bar(d, c, c, c, c, 1e6)); d += timedelta(days=1)
    return out


def test_panel_outcomes_settle_after_the_snapshot_date_against_the_benchmark(st):
    rows = [{"ticker": "WIN", "price": 100.0, "score": 70, "coverage": 1.0, "comps": {k: 0.7 for k in KEYS}}, {"ticker": "LOSE", "price": 100.0, "score": 40, "coverage": 1.0, "comps": {k: 0.3 for k in KEYS}}]
    SP.record_panel(st, rows, "q1", 90, 100, datetime(2026, 1, 12, 12, tzinfo=timezone.utc))
    series = {"WIN.NS": bars_from(date(2025, 11, 1), [100] * 50 + [100 + 1.0 * i for i in range(90)]), "LOSE.NS": bars_from(date(2025, 11, 1), [100] * 50 + [100 - 0.5 * i for i in range(90)]),
              "^NSEI": bars_from(date(2025, 11, 1), [20000 + 5 * i for i in range(140)])}
    fetch = lambda sym, **k: ({"bars": series[sym]} if sym in series else None)
    assert SP.evaluate_panel(st, fetch, today=date(2026, 1, 20)) == {"due": 0, "settled": 0}                  # 8 days later: nothing is due yet
    res = SP.evaluate_panel(st, fetch, today=date(2026, 5, 1)); assert res["settled"] == 4                   # 20 and 60 days, both stocks
    assert SP.evaluate_panel(st, fetch, today=date(2026, 5, 1))["due"] == 0                                    # idempotent
    p = SP.load_panel(st, 20); (d, items), = p.items()
    by = {tuple(sorted(r["comps"].items()))[0][1]: r["y"] for r in items}
    assert by[0.7] > 0 > by[0.3]                                                                               # the stock that rose beat the index, the one that fell lagged it
