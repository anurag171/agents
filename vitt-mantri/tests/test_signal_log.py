import json, random
from datetime import date, datetime, timedelta, timezone
import pytest
from engine import signal_log as L
from engine.store import SCHEMA_VERSION, Store
from engine.technicals import Bar

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)        # a Monday


@pytest.fixture
def st(tmp_path): return Store(str(tmp_path / "t.sqlite"))


def row(t="ONGC", action="BUY", pct=82, news_action=None, **kw):
    r = {"ticker": t, "action": action, "net": 4.0 if action != "SELL" else -4.0, "score_pct": pct, "strength": "Very strong", "data_coverage_pct": 100, "limited_data": False, "sources": ["Reuters"],
         "events": 2, "best_grade": "A", "headlines": ["Brent jumps"], "market": {"price": 100.0, "change_pct": 1.0, "mkt": "IN"}, "chart": {"verdict": "CONFIRM", "score": 0.6, "close": 100.0}, "signal_breakdown": []}
    if news_action: r["news_action"] = news_action
    r.update(kw); return r


def count(st, sql="SELECT COUNT(*) c FROM signals_log"):
    with st.conn() as c: return c.execute(sql).fetchone()["c"]


def test_schema_is_wal_versioned_and_idempotent(tmp_path):
    p = str(tmp_path / "x.sqlite"); Store(p); s2 = Store(p)
    with s2.conn() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal" and c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_a_call_is_logged_once_not_every_cycle(st):
    assert L.record(st, [row()], [], T0) == 1
    for i in range(1, 30): assert L.record(st, [row()], [], T0 + timedelta(seconds=90 * i)) == 0        # 30 more cycles, same call
    assert count(st) == 1


def test_flip_logs_new_call_watch_ends_call_and_flicker_is_debounced(st):
    L.record(st, [row()], [], T0)
    assert L.record(st, [row(action="SELL")], [], T0 + timedelta(days=1)) == 1                        # BUY -> SELL = new call
    L.record(st, [row(action="WATCH")], [], T0 + timedelta(days=2))                                   # call ended
    assert L.record(st, [row()], [], T0 + timedelta(days=3)) == 0                                     # BUY again 3 days after last BUY: flicker
    L.record(st, [row(action="WATCH")], [], T0 + timedelta(days=4))
    assert L.record(st, [row()], [], T0 + timedelta(days=8)) == 1                                     # genuinely new after >=5 days
    assert count(st) == 3


def test_shadow_calls_are_logged_with_reason_and_movers_are_not(st):
    gated = row("GATED", action="WATCH", pct=48, news_action="BUY", gated_by_strength=True)
    chart_blocked = row("CHT", action="WATCH", pct=70, news_action="BUY", chart={"verdict": "CONTRADICT", "score": -0.3, "close": 50})
    mover = {"ticker": "ZOMATO", "action": "WATCH", "net": 0, "origin": "market-mover", "score_pct": None}
    plain_watch = row("PW", action="WATCH")
    assert L.record(st, [gated, chart_blocked, mover, plain_watch], [], T0) == 2
    with st.conn() as c:
        got = {r["ticker"]: (r["news_action"], r["final_action"], r["gate_reason"]) for r in c.execute("SELECT * FROM signals_log")}
    assert got == {"GATED": ("BUY", "WATCH", "strength"), "CHT": ("BUY", "WATCH", "chart:contradict")}


def test_assets_are_logged_as_their_own_kind_with_yahoo_symbol(st):
    a = {"key": "gold", "yahoo": "GC=F", "action": "BUY", "news_score": 3.0, "score_pct": 78, "price": 2650.0, "sources": [], "evidence": [{"theme": "Gold bid"}]}
    L.record(st, [], [a], T0)
    with st.conn() as c: r = c.execute("SELECT * FROM signals_log").fetchone()
    assert (r["kind"], r["ticker"], r["yahoo"], r["price_at_signal"], r["mkt"]) == ("asset", "gold", "GC=F", 2650.0, None)


def bars(start, closes):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5: d += timedelta(days=1)
        out.append(Bar(d, c, c, c, c, 1e6)); d += timedelta(days=1)
    return out


def test_outcome_uses_next_closes_only_no_lookahead():
    # Thu Oct1=100, Fri=100, Mon Oct5 (EMISSION DAY)=150: the stock jumped on the day of the call. The call must not get credit for it.
    b = bars(date(2026, 10, 1), [100, 100, 150, 150, 151, 152, 153, 154, 155, 156, 300])
    d0 = date(2026, 10, 5)
    i0 = L._first_after(b, d0)
    assert b[i0 - 1].d == d0 and b[i0].d > d0                               # entry side = emission day, exit side strictly later
    o = L.compute_outcome(b, d0, +1, entry_logged=150.0, h=1)               # logged price already includes the jump
    assert o["entry_source"] == "logged" and o["ret_pct"] == pytest.approx(0.0)       # the +50% before/at the call is NOT counted
    o5 = L.compute_outcome(b, d0, +1, 150.0, 5)
    assert o5["exit_price"] == b[i0 + 4].c == 154 and o5["exit_price"] != 300          # the late 300 spike is beyond the horizon
    assert L.compute_outcome(b, d0, +1, 150.0, 20) is None                  # not enough future bars yet


def test_sell_sign_split_guard_and_benchmark_excess():
    b = bars(date(2026, 10, 1), [100, 100, 100, 95, 94, 93, 92, 91, 90, 89])
    bench = bars(date(2026, 10, 1), [1000] * 3 + [1010, 1020, 1030, 1040, 1050, 1060, 1070])
    o = L.compute_outcome(b, date(2026, 10, 5), -1, 100.0, 5, bench)
    assert o["signed_ret_pct"] > 0 and o["ret_pct"] < 0                         # SELL made money as price fell
    assert o["bench_ret_pct"] > 0 and o["signed_excess_pct"] > o["signed_ret_pct"]      # market rose, so the short beat it by more
    split = L.compute_outcome(b, date(2026, 10, 5), +1, 400.0, 1)               # logged 400 vs ~100 close: split/bad quote
    assert split["entry_source"] == "bars_close"


def test_evaluate_outcomes_end_to_end_idempotent_and_waits_for_horizons(st):
    L.record(st, [row("ABC", price_override=None, market={"price": 100.0, "change_pct": 1.0, "mkt": "IN"})], [], T0)
    series = {"ABC.NS": bars(date(2026, 9, 1), [100] * 25 + [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124]),
              "^NSEI": bars(date(2026, 9, 1), [1000 + i for i in range(50)])}
    fetch = lambda sym, **k: ({"bars": series[sym]} if sym in series else None)
    early = L.evaluate_outcomes(st, fetch, today=date(2026, 10, 6))                # only 1 day later: just the 1-day horizon is due
    assert early["due"] == 1 and early["settled"] == 1
    later = L.evaluate_outcomes(st, fetch, today=date(2026, 11, 10))
    assert later["settled"] == 2
    assert L.evaluate_outcomes(st, fetch, today=date(2026, 11, 10))["due"] == 0   # idempotent
    with st.conn() as c: hs = [r["horizon"] for r in c.execute("SELECT horizon FROM signal_outcomes ORDER BY horizon")]
    assert hs == [1, 5, 20]


def test_wilson_and_buckets_and_small_samples_are_not_reported_as_facts():
    lo, hi = L.wilson(64, 100); assert 0.54 < lo < 0.55 and 0.72 < hi < 0.74 and L.wilson(0, 0) == [0.0, 0.0]
    assert [L.bucket(p) for p in (5, 45, 59, 60, 82, 100)] == ["0-39%", "40-49%", "50-59%", "60-69%", "80-89%", "90-100%"]
    s = L.stats([{"signed_ret_pct": 1.0, "signed_excess_pct": 0.5}] * 12)
    assert s["n"] == 12 and s["enough"] is False and L.history_sentence(s, 5).startswith("Not enough history")


def seed(st, n, edge, seed_=3):
    """n settled calls where higher strength => higher chance of being right (edge=0 => pure noise)."""
    rnd = random.Random(seed_)
    with st.conn() as c:
        for i in range(n):
            pct = rnd.randint(35, 98); p_right = 0.5 + edge * (pct - 65) / 100
            right = rnd.random() < p_right; ret = rnd.uniform(0.5, 4) if right else -rnd.uniform(0.5, 4)
            cur = c.execute("INSERT INTO signals_log(emitted_at,emitted_date,kind,ticker,yahoo,mkt,direction,news_action,final_action,score_pct,chart_verdict,scoring_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (T0.isoformat(), f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "stock", f"T{i}", f"T{i}.NS", "IN", 1, "BUY", "BUY" if pct >= 60 else "WATCH", pct, "CONFIRM" if i % 3 == 0 else "NEUTRAL", "s1"))
            c.execute("INSERT INTO signal_outcomes(signal_id,horizon,signed_ret_pct,signed_excess_pct,bench_ret_pct) VALUES(?,?,?,?,?)", (cur.lastrowid, 5, ret, ret - 0.2, 0.3))


def test_performance_detects_real_signal_and_refuses_to_see_noise(st, tmp_path):
    seed(st, 400, edge=0.9); good = L.performance(st, 5)
    assert good["overall"]["n"] == 400 and good["predictive"]["predictive"] is True and good["predictive"]["ic"] > 0.1
    hi, lo = good["by_bucket"]["90-100%"], good["by_bucket"]["40-49%"]
    assert hi["hit_rate"] > lo["hit_rate"] and hi["enough"]
    assert set(good["by_chart"]) == {"CONFIRM", "NEUTRAL"} and set(good["by_shown"]) == {"shown as BUY/SELL", "held back to WATCH"}
    noise = Store(str(tmp_path / "n.sqlite")); seed(noise, 400, edge=0.0, seed_=11)
    assert L.performance(noise, 5)["predictive"]["predictive"] is False and "NOT been shown" in L.performance(noise, 5)["predictive"]["verdict"]


def test_attach_history_and_scoring_version_isolation(st):
    seed(st, 300, edge=0.9)
    with st.conn() as c: c.execute("UPDATE signals_log SET scoring_version='old' WHERE id<=150")
    perf = L.performance(st, 5); assert perf["overall"]["n"] == 150                   # old formula never mixed in
    rows = L.attach_history([{"score_pct": 85}, {"score_pct": None}], perf)
    assert rows[0]["history"]["bucket"] == "80-89%" and "history" not in rows[1] and rows[0]["history"]["text"]
    empty = L.attach_history([{"score_pct": 85}], L.performance(Store(st.path + ".2"), 5))
    assert empty[0]["history"]["n"] == 0 and "No settled calls yet" in empty[0]["history"]["text"]


def test_api_endpoints_and_csv_export_protected(tmp_path, monkeypatch):
    import app
    monkeypatch.setenv("VM_DB", str(tmp_path / "api.sqlite")); monkeypatch.setattr(app, "_PERF", {})
    s = Store(); seed(s, 60, 0.5); c = app.app.test_client()
    j = c.get("/api/performance?horizon=5").get_json()
    assert j["overall"]["n"] == 60 and j["horizon"] == 5 and c.get("/api/performance?horizon=99").get_json()["horizon"] == 5
    monkeypatch.setenv("VM_ADMIN_TOKEN", "k")
    assert c.get("/api/signals.csv").status_code == 401
    r = c.get("/api/signals.csv", headers={"X-Admin-Token": "k"}); assert r.status_code == 200 and r.data.decode().splitlines()[0].startswith("id,emitted_at") and len(r.data.decode().splitlines()) == 61


def test_concurrent_writers_and_readers_never_lock_or_corrupt(st):
    import threading
    errors = []
    def writer(k):
        try:
            for i in range(40): L.record(st, [row(f"W{k}_{i}")], [], T0 + timedelta(seconds=i))
        except Exception as e: errors.append(repr(e))
    def reader():
        try:
            for _ in range(60): L.performance(st, 5); L.export_csv(st, 50)
        except Exception as e: errors.append(repr(e))
    ts = [threading.Thread(target=writer, args=(k,)) for k in range(3)] + [threading.Thread(target=reader) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errors == [] and count(st) == 120
